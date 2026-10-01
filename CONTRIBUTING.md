# Contributing to Noble Search

Thanks for helping. Noble Search exists to make the military-to-civilian job search less painful, and every bug report, idea and fix helps.

## Ways to help (no coding needed)

- **Report a bug.** Use **Issues → New issue → Bug report**. Include what you clicked, what you expected, and what happened. Logs help: run `docker compose logs --tail 100`, and **remove anything personal or any API keys** before pasting.
- **Report an employer that can't be read.** Use the **"Employer can't be read"** form. Include the company name and its careers link.
- **Suggest a feature.** Use the **Feature request** form, or start a thread in **Discussions**.
- **Ask a question.** Use **Discussions → Q&A** instead of opening an issue.

## Contributing code

1. Fork the repo and create a branch from `main`.
2. Keep the project's rules:
   - Use the Python standard library only. The one exception is `python-docx`. Any new library must be pinned in `requirements.txt` and justified in the pull request.
   - The web pages are plain HTML, CSS and JavaScript in `static/`, with no build step. They must work in light and dark mode and at phone width.
   - Never send API keys to the browser. Never log keys or resume text.
   - Never try to get around a website's bot check or CAPTCHA.
   - Never let a search change a user's statuses, notes or "viewed" flags.
   - Use plain language in the interface and the docs.
3. Add or update tests in `tests/`, then run them:
   ```bash
   python3 -m unittest discover -s tests
   ```
4. Open a pull request that says what changed and why. If you changed a page, add a screenshot.

## License of contributions (please read)

Noble Search is released under the **PolyForm Noncommercial License 1.0.0**. Its copyright holders (Kenneth Haynes and CyberCloudAI) also offer separate commercial licenses. To keep that possible:

> **By submitting a contribution (code, documentation, images or other material) to this repository, you confirm that you have the right to submit it, and you grant Kenneth Haynes and CyberCloudAI a perpetual, worldwide, non-exclusive, royalty-free, irrevocable license to use, copy, modify, distribute and sublicense your contribution, including under commercial terms. You keep the copyright to your contribution.**

If you can't agree to this, please open an issue describing the change instead of sending code.

## Code of conduct

Be respectful and helpful. Many people here are in the middle of a hard transition. Harassment, discrimination or personal attacks will get comments removed and accounts blocked.
