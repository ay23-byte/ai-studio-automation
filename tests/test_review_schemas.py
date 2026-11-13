"""The published JSON schemas must describe what the code really writes."""
import json
import tempfile
import unittest
from pathlib import Path

from core import review_contract as contract
from scripts.apply_review_decisions import apply_decisions
from tests.review_package_fixture import approve, fake_media, make_package

try:
    import jsonschema
except ImportError:  # pragma: no cover - installed by requirements-dev.txt
    jsonschema = None

SCHEMAS = Path(__file__).resolve().parents[1] / "schemas"


def load(name):
    return json.loads((SCHEMAS / name).read_text(encoding="utf-8"))


@unittest.skipIf(jsonschema is None, "jsonschema is not installed")
class SchemaTests(unittest.TestCase):
    def errors(self, schema, document):
        validator = jsonschema.Draft202012Validator(load(schema))
        return [f"{list(error.path)}: {error.message[:160]}" for error in validator.iter_errors(document)]

    def test_schemas_are_valid_and_match_the_contract_constants(self):
        manifest, decisions = load("production-manifest-v2.schema.json"), load("review-decisions-v2.schema.json")
        jsonschema.Draft202012Validator.check_schema(manifest)
        jsonschema.Draft202012Validator.check_schema(decisions)
        self.assertEqual(manifest["properties"]["schema_version"]["const"], contract.MANIFEST_SCHEMA_VERSION)
        self.assertEqual(decisions["properties"]["schema_version"]["const"], contract.DECISIONS_SCHEMA_VERSION)
        self.assertEqual(set(manifest["properties"]["mode"]["enum"]), set(contract.MODES))
        self.assertEqual(set(decisions["properties"]["confirmations"]["required"]), set(contract.CONFIRMATIONS))
        self.assertEqual(set(manifest["properties"]["qa"]["required"]), set(contract.QA_FILES))
        origins = manifest["properties"]["scenes"]["items"]["properties"]["assets"]["items"]["properties"][
            "provenance"]["properties"]["origin"]["enum"]
        self.assertEqual(set(origins), set(contract.ORIGINS))

    def test_every_mode_writes_documents_that_satisfy_the_schemas(self):
        for mode in contract.MODES:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temp:
                root = Path(temp) / "package"
                manifest = make_package(root, mode)
                self.assertEqual(self.errors("production-manifest-v2.schema.json", manifest), [])
                template = json.loads((root / contract.TEMPLATE_NAME).read_text())
                self.assertEqual(self.errors("review-decisions-v2.schema.json", template), [])
                decisions = approve(root)
                self.assertEqual(self.errors("review-decisions-v2.schema.json", decisions), [])
                path = Path(temp) / "decisions.json"
                path.write_text(json.dumps(decisions))
                with fake_media(mode):
                    apply_decisions(root, path)
                applied = json.loads((root / contract.MANIFEST_NAME).read_text())
                self.assertEqual(self.errors("production-manifest-v2.schema.json", applied), [])

    def test_schema_rejects_what_the_contract_rejects(self):
        with tempfile.TemporaryDirectory() as temp:
            manifest = make_package(Path(temp) / "package", "shortform")
        for change in (lambda m: m.update(schema_version=1), lambda m: m.update(mode="podcast"),
                       lambda m: m["video"].update(sha256="short"), lambda m: m.update(scenes=[]),
                       lambda m: m["scenes"][0]["assets"][0]["provenance"].update(origin="trust me"),
                       lambda m: m["upload"].update(privacy_status="public")):
            broken = json.loads(json.dumps(manifest))
            change(broken)
            self.assertTrue(self.errors("production-manifest-v2.schema.json", broken))


if __name__ == "__main__":
    unittest.main()
