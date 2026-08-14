-- FULL_PROJECT Finding 的覆盖报告是可审计事实，不能只存在于模型文本中。
ALTER TABLE findings ADD COLUMN coverage_report_id TEXT REFERENCES coverage_reports(id) ON DELETE RESTRICT;

CREATE INDEX findings_coverage_report_idx ON findings(coverage_report_id);
