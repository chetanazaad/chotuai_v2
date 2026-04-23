"""Readiness Reporter — structured validation report generator."""
import json
import os
from datetime import datetime, timezone
from pathlib import Path


def generate_report(all_results: list, output_dir: Path) -> dict:
    """Generate validation_report.json and validation_summary.md."""
    output_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()

    categories = {}
    for r in all_results:
        cat = r.category
        if cat not in categories:
            categories[cat] = {"total": 0, "passed": 0, "failed": 0, "skipped": 0, "tests": []}
        categories[cat]["total"] += 1
        if r.passed:
            categories[cat]["passed"] += 1
        else:
            categories[cat]["failed"] += 1
        categories[cat]["tests"].append({
            "name": r.name,
            "passed": r.passed,
            "duration_ms": r.duration_ms,
            "detail": r.detail,
            "error": r.error[:500] if r.error else "",
        })

    total = len(all_results)
    passed = sum(1 for r in all_results if r.passed)
    failed = total - passed
    all_passed = failed == 0

    if all_passed:
        readiness = "READY"
        readiness_note = "All tests passed. System is production-safe."
    elif failed <= 2:
        readiness = "CONDITIONAL"
        readiness_note = f"{failed} test(s) failed. Review failures before deployment."
    else:
        readiness = "NOT READY"
        readiness_note = f"{failed} test(s) failed. System requires fixes."

    failure_traces = []
    for r in all_results:
        if not r.passed:
            failure_traces.append({
                "test": r.name,
                "category": r.category,
                "detail": r.detail,
                "error": r.error[:1000] if r.error else "",
            })

    recommendations = []
    failed_categories = [cat for cat, data in categories.items() if data["failed"] > 0]
    for cat in failed_categories:
        recommendations.append(f"Fix failures in '{cat}' category ({categories[cat]['failed']} failed)")

    if not failed_categories:
        recommendations.append("No fixes needed. System is stable.")

    from chotu_ai import __version__
    report = {
        "timestamp": now,
        "version": __version__,
        "readiness": readiness,
        "readiness_note": readiness_note,
        "totals": {
            "total": total,
            "passed": passed,
            "failed": failed,
            "pass_rate": round(passed / max(total, 1) * 100, 1),
        },
        "categories": categories,
        "failure_traces": failure_traces,
        "recommendations": recommendations,
    }

    report_file = output_dir / "validation_report.json"
    temp_file = output_dir / "validation_report.json.tmp"
    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    os.replace(str(temp_file), str(report_file))

    _write_markdown_summary(report, output_dir)

    return report


def _write_markdown_summary(report: dict, output_dir: Path) -> None:
    """Write human-readable validation_summary.md."""
    lines = []
    lines.append(f"# Validation Report — chotu_ai v{report['version']}")
    lines.append("")
    lines.append(f"**Generated:** {report['timestamp']}")
    lines.append(f"**Readiness:** {report['readiness']}")
    lines.append(f"**Note:** {report['readiness_note']}")
    lines.append("")

    totals = report["totals"]
    lines.append("## Summary")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|---|---|")
    lines.append(f"| Total Tests | {totals['total']} |")
    lines.append(f"| Passed | {totals['passed']} |")
    lines.append(f"| Failed | {totals['failed']} |")
    lines.append(f"| Pass Rate | {totals['pass_rate']}% |")
    lines.append("")

    lines.append("## Categories")
    lines.append("")
    lines.append("| Category | Total | Passed | Failed |")
    lines.append("|---|---|---|---|")
    for cat_name, cat_data in report["categories"].items():
        icon = "✅" if cat_data["failed"] == 0 else "❌"
        lines.append(f"| {icon} {cat_name} | {cat_data['total']} | {cat_data['passed']} | {cat_data['failed']} |")
    lines.append("")

    if report["failure_traces"]:
        lines.append("## Failures")
        lines.append("")
        for ft in report["failure_traces"]:
            lines.append(f"### ❌ {ft['test']} ({ft['category']})")
            lines.append(f"**Detail:** {ft['detail']}")
            if ft["error"]:
                lines.append("```")
                lines.append(ft["error"][:500])
                lines.append("```")
            lines.append("")

    lines.append("## Recommendations")
    lines.append("")
    for rec in report["recommendations"]:
        lines.append(f"- {rec}")

    summary_file = output_dir / "validation_summary.md"
    summary_file.write_text("\n".join(lines), encoding="utf-8")