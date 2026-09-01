import json
import tempfile
import unittest
from pathlib import Path

from scripts.verify_deployment import ANDROID_FINGERPRINT, verify_deployment


class DeploymentVerifierTests(unittest.TestCase):
    def make_site(self) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        (root / ".well-known").mkdir()
        (root / "torneo" / "registro").mkdir(parents=True)
        (root / ".nojekyll").touch()
        (root / "CNAME").write_text("www.iqdominoes.com", encoding="utf-8")
        (root / "index.html").write_text("<a href='/about/'>About</a>", encoding="utf-8")
        (root / "about").mkdir()
        (root / "about" / "index.html").touch()
        for route in ("join", "partners", "privacy", "terms", "es/privacy", "es/terms"):
            (root / route).mkdir(parents=True, exist_ok=True)
            (root / route / "index.html").touch()
        (root / "partners" / "index.html").write_text(
            "<script>fetch('https://api.iqdominoes.com/api/public/partners')</script>",
            encoding="utf-8",
        )
        for route in ("torneo", "torneo/registro"):
            (root / route / "index.html").write_text(
                "<h1>This page is not available.</h1>", encoding="utf-8"
            )
        (root / "icon.png").touch()
        (root / ".well-known" / "assetlinks.json").write_text(
            json.dumps([{
                "relation": ["delegate_permission/common.handle_all_urls"],
                "target": {
                    "namespace": "android_app",
                    "package_name": "com.iqdominoes.app",
                    "sha256_cert_fingerprints": [ANDROID_FINGERPRINT],
                },
            }]),
            encoding="utf-8",
        )
        (root / ".well-known" / "apple-app-site-association").write_text(
            json.dumps({"applinks": {"apps": [], "details": [{
                "appID": "2CHGB59PS9.com.iqdominoes.app", "paths": ["/join/*"],
            }]}}),
            encoding="utf-8",
        )
        (root / "manifest.webmanifest").write_text(
            json.dumps({"start_url": "/", "scope": "/", "icons": [{"src": "/icon.png"}]}),
            encoding="utf-8",
        )
        (root / "torneo" / "roster.json").write_text(
            json.dumps({"updatedAt": "2026-08-31", "slots": []}), encoding="utf-8"
        )
        return root

    def test_valid_fixture_passes(self):
        self.assertEqual([], verify_deployment(self.make_site()))

    def test_reports_missing_critical_file(self):
        root = self.make_site()
        (root / ".nojekyll").unlink()
        self.assertIn(".nojekyll: required regular file is missing or is a symlink", verify_deployment(root))

    def test_rejects_cname_drift(self):
        root = self.make_site()
        (root / "CNAME").write_text("staging.iqdominoes.com", encoding="utf-8")
        self.assertTrue(any("expected one hostname line" in error for error in verify_deployment(root)))

    def test_accepts_line_terminated_cname(self):
        root = self.make_site()
        (root / "CNAME").write_text("www.iqdominoes.com\n", encoding="utf-8")
        self.assertEqual([], verify_deployment(root))

    def test_reports_missing_required_compatibility_route(self):
        root = self.make_site()
        (root / "join" / "index.html").unlink()
        self.assertIn(
            "join/index.html: required compatibility route is missing or is a symlink",
            verify_deployment(root),
        )

    def test_requires_runtime_partner_contract(self):
        root = self.make_site()
        (root / "partners" / "index.html").write_text(
            "<p>Packages from $750.</p>", encoding="utf-8"
        )
        self.assertIn(
            "partners/index.html: must fetch the sponsor-safe runtime contract",
            verify_deployment(root),
        )

    def test_rejects_reactivated_tournament_surface(self):
        root = self.make_site()
        (root / "torneo" / "index.html").write_text(
            "<form action='/api/torneo/register'><button>Register now</button></form>",
            encoding="utf-8",
        )
        errors = verify_deployment(root)
        self.assertTrue(any("dormant compatibility page marker is missing" in error for error in errors))
        self.assertTrue(any("forbidden marker '<form'" in error for error in errors))
        self.assertTrue(any("forbidden marker '/api/torneo'" in error for error in errors))

    def test_accepts_a_symlinked_site_root(self):
        root = self.make_site()
        alias_directory = tempfile.TemporaryDirectory()
        self.addCleanup(alias_directory.cleanup)
        alias = Path(alias_directory.name) / "site"
        alias.symlink_to(root, target_is_directory=True)
        self.assertEqual([], verify_deployment(alias))

    def test_reports_malformed_json(self):
        root = self.make_site()
        (root / "manifest.webmanifest").write_text("{", encoding="utf-8")
        errors = verify_deployment(root)
        self.assertTrue(any("manifest.webmanifest: invalid JSON" in error for error in errors))

    def test_reports_missing_file_and_directory_index_link_with_lines(self):
        root = self.make_site()
        (root / "index.html").write_text(
            "<img src='missing.png'>\n<a href='/empty/'>empty</a>", encoding="utf-8"
        )
        (root / "empty").mkdir()
        errors = verify_deployment(root)
        self.assertTrue(any("index.html:1:" in error and "missing.png" in error for error in errors))
        self.assertTrue(any("index.html:2:" in error and "/empty/" in error for error in errors))

    def test_rejects_route_directory_with_external_symlinked_index(self):
        root = self.make_site()
        (root / "index.html").write_text("<a href='/dir/'>unsafe route</a>", encoding="utf-8")
        route = root / "dir"
        route.mkdir()
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as temporary:
            outside_index = Path(temporary.name)
        self.addCleanup(outside_index.unlink)
        (route / "index.html").symlink_to(outside_index)

        errors = verify_deployment(root)
        self.assertTrue(any(
            "index.html:1:" in error and "/dir/" in error and "does not resolve" in error
            for error in errors
        ))

    def test_does_not_read_a_symlinked_html_document(self):
        root = self.make_site()
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as temporary:
            outside_html = Path(temporary.name)
        self.addCleanup(outside_html.unlink)
        (root / "outside.html").symlink_to(outside_html)

        errors = verify_deployment(root)
        self.assertIn(
            "outside.html: HTML document is a symlink or not a regular file",
            errors,
        )

    def test_ignores_external_fragment_and_query_references(self):
        root = self.make_site()
        (root / "index.html").write_text(
            "<a href='https://example.test/nope'>x</a><a href='//example.test/nope'>x</a>"
            "<a href='mailto:hello@example.test'>x</a><a href='tel:+15551212'>x</a>"
            "<img src='data:image/png,abc'><a href='javascript:void(0)'>x</a>"
            "<a href='#section'>x</a><a href='/about/?from=test#section'>x</a>",
            encoding="utf-8",
        )
        self.assertEqual([], verify_deployment(root))

    def test_rejects_percent_decoded_traversal(self):
        root = self.make_site()
        (root / "index.html").write_text("<a href='/%2e%2e/outside.html'>bad</a>", encoding="utf-8")
        errors = verify_deployment(root)
        self.assertTrue(any("index.html:1:" in error and "escapes the site root" in error for error in errors))

    def test_resolves_relative_references_against_a_local_base(self):
        root = self.make_site()
        (root / "assets").mkdir()
        (root / "assets" / "logo.png").touch()
        (root / "pages").mkdir()
        (root / "pages" / "page.html").write_text(
            "<base href='/assets/'><img src='logo.png'>", encoding="utf-8"
        )
        self.assertEqual([], verify_deployment(root))

    def test_external_base_makes_relative_references_external(self):
        root = self.make_site()
        (root / "index.html").write_text(
            "<base href='https://cdn.example.test/static/'><img src='missing.png'>",
            encoding="utf-8",
        )
        self.assertEqual([], verify_deployment(root))

    def test_rejects_unsafe_local_base(self):
        root = self.make_site()
        (root / "index.html").write_text(
            "<base href='/%2e%2e/outside/'><img src='safe.png'>", encoding="utf-8"
        )
        errors = verify_deployment(root)
        self.assertTrue(any("index.html:1: base href" in error and "escapes the site root" in error for error in errors))

    def test_rejects_symlink_artifact_and_symlinked_parent(self):
        root = self.make_site()
        target = root / "real-nojekyll"
        target.touch()
        (root / ".nojekyll").unlink()
        (root / ".nojekyll").symlink_to(target)
        errors = verify_deployment(root)
        self.assertTrue(any(error.startswith(".nojekyll:") and "symlink" in error for error in errors))

        assetlinks = (root / ".well-known" / "assetlinks.json").read_text(encoding="utf-8")
        apple = (root / ".well-known" / "apple-app-site-association").read_text(encoding="utf-8")
        (root / ".well-known" / "assetlinks.json").unlink()
        (root / ".well-known" / "apple-app-site-association").unlink()
        (root / ".well-known").rmdir()
        real_parent = root / "real-well-known"
        real_parent.mkdir()
        (real_parent / "assetlinks.json").write_text(assetlinks, encoding="utf-8")
        (real_parent / "apple-app-site-association").write_text(apple, encoding="utf-8")
        (root / ".well-known").symlink_to(real_parent, target_is_directory=True)
        errors = verify_deployment(root)
        self.assertTrue(any(error.startswith(".well-known/assetlinks.json:") and "symlink" in error for error in errors))
        self.assertTrue(any(error.startswith(".well-known/apple-app-site-association:") and "symlink" in error for error in errors))

    def test_reports_invalid_roster_shape_and_duplicate_slots(self):
        root = self.make_site()
        (root / "torneo" / "roster.json").write_text(
            json.dumps({"updatedAt": 4, "slots": [
                {"n": 1, "handle": "One"}, {"n": 1, "handle": "Two"}, {"n": 17, "handle": ""}, "wrong",
            ]}),
            encoding="utf-8",
        )
        errors = verify_deployment(root)
        self.assertTrue(any("updatedAt must be a string" in error for error in errors))
        self.assertTrue(any("duplicates slot number 1" in error for error in errors))
        self.assertTrue(any("integer from 1 through 16" in error for error in errors))
        self.assertTrue(any("handle must be a nonempty string" in error for error in errors))
        self.assertTrue(any("slots[3] must be an object" in error for error in errors))

    def test_rejects_all_association_semantic_drift(self):
        root = self.make_site()
        (root / ".well-known" / "apple-app-site-association").write_text(
            json.dumps({"applinks": {"apps": [], "details": [{
                "appID": "2CHGB59PS9.com.iqdominoes.app", "paths": ["NOT /join/*", "/join/*"],
            }], "extra": True}}),
            encoding="utf-8",
        )
        errors = verify_deployment(root)
        self.assertTrue(any("must exactly contain the intended Apple association" in error for error in errors))

        root = self.make_site()
        (root / ".well-known" / "assetlinks.json").write_text(
            json.dumps([{
                "relation": ["delegate_permission/common.handle_all_urls", "extra"],
                "target": {
                    "namespace": "android_app",
                    "package_name": "com.iqdominoes.app",
                    "sha256_cert_fingerprints": ["WRONG", ANDROID_FINGERPRINT],
                },
            }]),
            encoding="utf-8",
        )
        errors = verify_deployment(root)
        self.assertTrue(any("must exactly contain the intended Android association" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
