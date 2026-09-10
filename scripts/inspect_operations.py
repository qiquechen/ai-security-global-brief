"""按运行ID汇总模型调用与阶段耗时，支持查看完整响应。"""
from __future__ import annotations
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ai_digest import config


def summarize(records):
    calls = [r for r in records if r.get("stage") == "llm.request"
             and r.get("status") in {"completed", "failed"}]
    totals = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens",
                "prompt_cache_hit_tokens", "prompt_cache_miss_tokens", "reasoning_tokens"):
        values = [(r.get("usage") or {}).get(key) for r in calls]
        known = [v for v in values if isinstance(v, int) and not isinstance(v, bool)]
        totals[key] = {"known_total": sum(known), "missing_calls": len(values) - len(known)}
    return dict(request_attempts=len(calls), failed_attempts=sum(r["status"] == "failed" for r in calls),
                response_models=dict(Counter(r.get("response_model") or "unknown" for r in calls)),
                usage=totals, llm_elapsed_seconds=round(sum(r.get("elapsed_seconds", 0) for r in calls), 3),
                stages=[{k: r[k] for k in ("stage", "status", "elapsed_seconds", "url", "exit_code") if k in r}
                        for r in records if r.get("status") in {"completed", "failed"}
                        and r.get("stage") != "llm.request"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", help="默认选择最近一条事件所属运行")
    parser.add_argument("--log-dir", type=Path, default=config.LOG_DIR)
    parser.add_argument("--responses", action="store_true", help="同时输出该运行的逐次响应详情")
    args = parser.parse_args()
    records = []
    malformed = 0
    for path in sorted(args.log_dir.glob("operations-*.jsonl")):
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    value = json.loads(line)
                    if isinstance(value, dict):
                        records.append(value)
                except json.JSONDecodeError:
                    malformed += 1
    if not records:
        parser.error("没有可读取的操作日志")
    run_id = args.run_id or records[-1].get("run_id")
    selected = [r for r in records if r.get("run_id") == run_id]
    if not selected:
        parser.error("未找到指定run_id")
    result = dict(run_id=run_id, malformed_lines=malformed, **summarize(selected))
    if args.responses:
        result["responses"] = [r for r in selected if r.get("stage") == "llm.request"
                               and r.get("status") != "started"]
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
