-- Phase 11：把用户问题和 Session Project 归属作为可恢复运行的稳定元数据。
-- 原始 prompt 保存在 Workspace 的不可变 PROMPT.json；Runtime 只保存引用与 hash，
-- 避免模型载荷、PII 或凭据进入控制面数据库。
ALTER TABLE sessions ADD COLUMN project_id TEXT REFERENCES projects(id) ON DELETE RESTRICT;
ALTER TABLE tasks ADD COLUMN prompt_ref TEXT;
ALTER TABLE tasks ADD COLUMN prompt_hash TEXT;

CREATE INDEX sessions_project_idx ON sessions(project_id, created_at);
CREATE INDEX tasks_session_idx ON tasks(session_id, created_at);
