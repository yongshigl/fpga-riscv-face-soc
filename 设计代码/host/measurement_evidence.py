"""Keep displayed measurements bound to their model, reference and firmware."""
import json
import math
from pathlib import Path

from board_evidence import file_hash


def read_measurement(path):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def matches_running_identity(report, identity):
    if not report or not identity or report.get("complete") is not True:
        return False
    measured = report.get("board")
    fields = ("firmware_version", "accelerator_version", "cpu_hz", "ai_hz", "uart_baud")
    return isinstance(measured, dict) and all(
        field in identity and measured.get(field) == identity[field] for field in fields)


def current_reference_accuracy(report_path, models, reference, threshold_path, pairs_path):
    try:
        report = read_measurement(report_path)
        threshold = read_measurement(threshold_path)
        if (not report or not threshold or threshold["devtest_labels_used_for_selection"]
                or threshold["devtest_image_overlap"]):
            return None
        provenance = report["provenance"]
        if (provenance["reference_sha256"] != file_hash(reference)
                or provenance["pairs_sha256"] != file_hash(pairs_path)
                or report["threshold_training_file_sha256"] != file_hash(threshold_path)):
            return None
        for name in ("yunet", "mobilefacenet"):
            manifest = read_measurement(Path(models) / (name + ".json"))
            if (not manifest or provenance[name] != manifest["image_sha256"]
                    or provenance[name] != file_hash(Path(models) / (name + "_image.bin"))
                    or threshold["model_hashes"][name] != manifest["model_sha256"]):
                return None
        selected = report["metrics"]["devtrain_selected"]
        accuracy = selected["accuracy_all_pairs_with_failures_counted_wrong"]
        if (selected["pairs"] != 1000 or type(selected["correct_pairs"]) is not int
                or not 0 <= selected["correct_pairs"] <= selected["pairs"]
                or type(accuracy) not in (int, float) or not math.isfinite(accuracy)
                or accuracy != selected["correct_pairs"] / selected["pairs"]
                or selected["threshold"] != threshold["threshold"]):
            return None
        return {"dataset": report["dataset"], "execution": report["execution"],
                "accuracy": accuracy, "pairs": selected["pairs"], "threshold": selected["threshold"],
                "threshold_training_file_sha256": report["threshold_training_file_sha256"],
                "provenance": provenance, "report_sha256": file_hash(report_path),
                "evaluated_utc": report["evaluated_utc"]}
    except (OSError, KeyError, TypeError, ValueError):
        return None
