# IQ Dominoes site

This is the static production companion site deployed through GitHub Pages.
There is no build manifest; edits publish as static files.

- Preserve `.nojekyll`, `CNAME`, `.well-known/apple-app-site-association`, and
  `.well-known/assetlinks.json`. They are compatibility surfaces for deployed
  mobile clients.
- Preserve canonical routes, privacy/terms pages, legacy deep-link paths, and
  tournament JSON/HTML shapes consumed by the app or server.
- Validate JSON syntax, internal links, and changed pages locally before
  publishing. Run `python3 scripts/verify_deployment.py` and
  `python3 -m unittest -v tests.test_verify_deployment`. A clean static render
  is not proof that app links still work.
- Do not publish by pushing or changing DNS/app-store association data without
  explicit authorization.
- GitHub Pages currently publishes `main` from the repository root. Treat every
  tracked file here as potentially public; never add secrets or private data.
