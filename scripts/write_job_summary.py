"""Write the job summary and ``run_report.json`` for a production workflow run.

Used as the last reporting step of a workflow (``if: always()``).  It only reads
what the run left on disk, so it is safe after a failure, and it always exits 0:
a reporting problem must never change the result of a production run.

    python -m scripts.write_job_summary --pipeline shorts --artifact shorts-review-123
"""
import argparse
import os
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _flag(value) -> bool | None:
    if value is None or str(value).strip() == "":
        return None
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pipeline", required=True,
                        help="shorts, longform (or long/documentary), news_short (or news)")
    parser.add_argument("--job-id", type=int, default=None, help="Report this job instead of the newest one")
    parser.add_argument("--job-status", default=os.getenv("JOB_STATUS"),
                        help="Workflow job status so far (success/failure/cancelled); defaults to $JOB_STATUS")
    parser.add_argument("--artifact", default=None, help="Name of the artifact this run uploads")
    parser.add_argument("--upload-requested", default=os.getenv("UPLOAD_REQUESTED"),
                        help="true if this run was meant to upload; defaults to $UPLOAD_REQUESTED")
    parser.add_argument("--output", default=None, help="Where to write run_report.json")
    parser.add_argument("--upload-result", default=None,
                        help="Result JSON of scripts/upload_documentary_artifact.py, if that uploader was used")
    parser.add_argument("--upload-failure", default=None,
                        help="Failure JSON of scripts/upload_documentary_artifact.py")
    args = parser.parse_args(argv)
    try:
        from core import run_report
        report = run_report.collect(args.pipeline, args.job_id, job_status=args.job_status,
                                    artifact=args.artifact, upload_requested=_flag(args.upload_requested),
                                    upload_result=args.upload_result, upload_failure=args.upload_failure)
        markdown = run_report.to_markdown(report)
        saved = run_report.write(report, args.output)
        summary = os.getenv("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a", encoding="utf-8") as handle:
                handle.write(markdown)
        else:
            print(markdown)
        line = run_report.annotation(report)
        if line and os.getenv("GITHUB_ACTIONS"):
            print(line)
        print(f"Run report: outcome={report.get('outcome')} job={report.get('job_id')} "
              f"saved={saved if saved else 'no'}")
    except Exception:  # reporting must never fail a production run
        print("WARNING: the job summary could not be written:", file=sys.stderr)
        traceback.print_exc()
        summary = os.getenv("GITHUB_STEP_SUMMARY")
        if summary:
            try:
                with open(summary, "a", encoding="utf-8") as handle:
                    handle.write("## Job summary unavailable\n\nThe summary step itself failed; "
                                 "see its log. The result of the run is unaffected.\n")
            except OSError:
                pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
