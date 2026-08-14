"""只读本地 Skill 注册表与渐进式加载实现。"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path, PurePath

from pydantic import BaseModel, ConfigDict, Field

from dataharness.domain import ContentHash, compute_content_hash

from .errors import SkillChangedError, SkillNotFoundError, UnsafeSkillError

_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class SkillDescriptor(BaseModel):
    """未激活 Skill 的最小公开描述，不暴露主机路径。"""

    model_config = ConfigDict(frozen=True)

    name: str
    description: str = Field(max_length=1000)
    content_hash: ContentHash


class LoadedSkill(BaseModel):
    """已经通过哈希校验、可注入 Agent 指令的 Skill 内容。"""

    model_config = ConfigDict(frozen=True)

    descriptor: SkillDescriptor
    content: str = Field(min_length=1)


class SkillScript(BaseModel):
    """Skill 脚本的只读内容；脚本仍必须交给 AnalysisRuntime 执行。"""

    model_config = ConfigDict(frozen=True)

    skill_name: str
    script_name: str
    content_hash: ContentHash
    code: str = Field(min_length=1)


class SkillRegistry:
    """管理管理员预装的本地 Skill，并在每次使用前重新校验内容哈希。"""

    def __init__(
        self,
        root: Path,
        *,
        max_skill_bytes: int = 256 * 1024,
        max_script_bytes: int = 512 * 1024,
    ) -> None:
        if max_skill_bytes <= 0 or max_script_bytes <= 0:
            raise ValueError("Skill 文件大小上限必须为正数")
        if root.is_symlink():
            raise UnsafeSkillError("Skill 根目录不能是符号链接")
        self._root = root.absolute().resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        self._max_skill_bytes = max_skill_bytes
        self._max_script_bytes = max_script_bytes
        self._active: dict[str, SkillDescriptor] = {}

    @property
    def root(self) -> Path:
        """仅供 Host 配置和诊断使用的已解析根目录。"""
        return self._root

    @staticmethod
    def _safe_name(name: str) -> str:
        normalized = unicodedata.normalize("NFKC", name.strip())
        if not _SAFE_NAME.fullmatch(normalized):
            raise UnsafeSkillError("Skill 名称包含不允许的路径字符")
        return normalized

    @staticmethod
    def _safe_script_name(name: str) -> str:
        normalized = unicodedata.normalize("NFKC", name.strip())
        if not normalized or normalized in {".", ".."} or PurePath(normalized).name != normalized:
            raise UnsafeSkillError("Skill 脚本名必须是单个文件名")
        return SkillRegistry._safe_name(normalized)

    def _skill_dir(self, name: str, *, must_exist: bool = True) -> Path:
        safe_name = self._safe_name(name)
        candidate = self._root / safe_name
        resolved = candidate.resolve(strict=False)
        if not resolved.is_relative_to(self._root):
            raise UnsafeSkillError("Skill 路径解析后越出受控根目录")
        if candidate.is_symlink():
            raise UnsafeSkillError("Skill 目录不能是符号链接")
        if must_exist and not candidate.is_dir():
            raise SkillNotFoundError(f"Skill 不存在：{safe_name}")
        return candidate

    def _skill_file(self, name: str) -> Path:
        directory = self._skill_dir(name)
        target = directory / "SKILL.md"
        if target.is_symlink() or not target.is_file():
            raise UnsafeSkillError("Skill 必须包含普通文件 SKILL.md")
        return target

    def _read_limited(self, path: Path, limit: int) -> bytes:
        size = path.stat().st_size
        if size > limit:
            raise UnsafeSkillError(f"Skill 文件超过 {limit} 字节上限")
        return path.read_bytes()

    @staticmethod
    def _description(content: str) -> str:
        for line in content.splitlines():
            value = line.strip()
            if not value or value.startswith("---"):
                continue
            if value.startswith("#"):
                value = value.lstrip("#").strip()
            return value[:1000]
        return "未提供描述"

    def _descriptor_from_file(self, name: str) -> SkillDescriptor:
        content = self._read_limited(self._skill_file(name), self._max_skill_bytes)
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as error:
            raise UnsafeSkillError("SKILL.md 必须是 UTF-8 文本") from error
        return SkillDescriptor(
            name=self._safe_name(name),
            description=self._description(text),
            content_hash=compute_content_hash(content),
        )

    def discover(self) -> tuple[SkillDescriptor, ...]:
        """列出本地预装 Skill；只扫描根目录的直接子目录。"""
        descriptors: list[SkillDescriptor] = []
        for child in sorted(self._root.iterdir(), key=lambda item: item.name.casefold()):
            if not child.is_dir():
                continue
            if child.is_symlink():
                raise UnsafeSkillError("Skill 根目录下不能出现符号链接目录")
            descriptors.append(self._descriptor_from_file(child.name))
        return tuple(descriptors)

    def descriptor(self, name: str) -> SkillDescriptor:
        """读取当前文件内容对应的描述，但不加载正文。"""
        return self._descriptor_from_file(name)

    def load(self, name: str) -> LoadedSkill:
        """按需加载一个 Skill，并确保描述哈希与正文哈希一致。"""
        descriptor = self._descriptor_from_file(name)
        content = self._read_limited(self._skill_file(name), self._max_skill_bytes)
        if compute_content_hash(content) != descriptor.content_hash:
            raise SkillChangedError(f"Skill 已变化：{descriptor.name}")
        return LoadedSkill(descriptor=descriptor, content=content.decode("utf-8"))

    def activate(self, name: str, *, expected_hash: ContentHash | None = None) -> LoadedSkill:
        """激活 Skill；激活后每次脚本调用都会重新验证哈希。"""
        loaded = self.load(name)
        if expected_hash is not None and loaded.descriptor.content_hash != expected_hash:
            raise SkillChangedError(f"Skill 哈希与 Run 记录不一致：{loaded.descriptor.name}")
        self._active[loaded.descriptor.name] = loaded.descriptor
        return loaded

    def active_descriptors(self) -> tuple[SkillDescriptor, ...]:
        """返回当前 Run 已激活的 Skill 描述。"""
        return tuple(self._active[name] for name in sorted(self._active))

    def verify_unchanged(self, descriptor: SkillDescriptor) -> None:
        """验证已激活 Skill 未被修改；失败时阻止继续执行旧脚本。"""
        current = self._descriptor_from_file(descriptor.name)
        if current.content_hash != descriptor.content_hash:
            raise SkillChangedError(f"Skill 已变化，请创建新的 Run：{descriptor.name}")

    def list_scripts(self, name: str) -> tuple[str, ...]:
        """列出 Skill scripts 目录中的普通文件名，不执行也不导入脚本。"""
        self.verify_unchanged(self._active[name]) if name in self._active else self.load(name)
        directory = self._skill_dir(name) / "scripts"
        if not directory.exists():
            return ()
        if directory.is_symlink() or not directory.is_dir():
            raise UnsafeSkillError("Skill scripts 必须是普通目录")
        names: list[str] = []
        for child in sorted(directory.iterdir(), key=lambda item: item.name.casefold()):
            if child.is_symlink() or not child.is_file():
                raise UnsafeSkillError("Skill scripts 只能包含普通文件")
            names.append(self._safe_script_name(child.name))
        return tuple(names)

    def load_active_script(self, skill_name: str, script_name: str) -> SkillScript:
        """只加载已激活 Skill 的脚本，并把脚本交给上层安全运行时。"""
        descriptor = self._active.get(skill_name)
        if descriptor is None:
            raise SkillNotFoundError(f"Skill 尚未激活：{skill_name}")
        self.verify_unchanged(descriptor)
        safe_script = self._safe_script_name(script_name)
        directory = self._skill_dir(skill_name) / "scripts"
        if directory.is_symlink() or not directory.is_dir():
            raise SkillNotFoundError(f"Skill 没有 scripts 目录：{skill_name}")
        target = directory / safe_script
        if target.is_symlink() or not target.is_file():
            raise SkillNotFoundError(f"Skill 脚本不存在：{skill_name}/{safe_script}")
        resolved = target.resolve(strict=False)
        if not resolved.is_relative_to(self._root):
            raise UnsafeSkillError("Skill 脚本路径解析后越出受控根目录")
        content = self._read_limited(target, self._max_script_bytes)
        try:
            code = content.decode("utf-8")
        except UnicodeDecodeError as error:
            raise UnsafeSkillError("Skill 脚本必须是 UTF-8 文本") from error
        return SkillScript(
            skill_name=skill_name,
            script_name=safe_script,
            content_hash=compute_content_hash(content),
            code=code,
        )
