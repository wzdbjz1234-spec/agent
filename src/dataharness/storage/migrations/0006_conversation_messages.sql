-- Chat-first migration：对话消息独立于 Task/Run 持久化。
-- content 只在用户显式保存对话时写入；模型原始 tool payload 仍不得进入 Runtime DB。
CREATE TABLE conversation_messages (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE RESTRICT,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX conversation_messages_session_idx
    ON conversation_messages(project_id, session_id, created_at, id);
