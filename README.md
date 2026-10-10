# Auto apply

Local agent that finds software engineering roles and applies where a public form exists.

The cycle is an agent loop. The model calls tools (`list_jobs`, `retrieve_resume`, `submit_application`, `skip_job`). `retrieve_resume` is a small RAG index over the resume sections in `config/profile.yaml`. A cover letter is rejected unless it uses facts from those passages. Work authorization, notice period, and compensation stay on the profile so the model cannot invent them.

Set `ANTHROPIC_API_KEY` and optionally `LLM_MODEL` (default `claude-sonnet-5`).

It runs every 4 hours and can be triggered immediately:

- Dashboard: http://127.0.0.1:8788
- `POST /api/runs`
- `POST /api/runs?dry_run=true` discovers jobs and does not submit
- `python -m autoapply run --limit 5`

## What it submits

Greenhouse career pages for backend, full-stack, staff, tech-lead, and similar roles in India or marked remote with no country restriction. The resume, notice period, and compensation answers come from `config/profile.yaml`.

Greenhouse often answers the submit with a captcha check. Those roles stay in the dashboard with the apply link instead of being guessed through. Boards that redirect to a company site (Okta, Toast, and similar) are saved the same way.

Other public feeds (Remotive, Jobicy, Remote OK, Himalayas, We Work Remotely) are saved on the dashboard as manual links. LinkedIn, Naukri, Instahyre, Hirist, and Wellfound need a logged-in session and are linked from the dashboard rather than scripted.

Set SMTP variables from `.env.example` if you later want email applications sent automatically. Until then they are not sent.

## Run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
PYTHONPATH=src python -m autoapply serve
```

Keep that process running. The daily cap is 80 submissions, at most 20 per cycle and 5 per company, so one employer does not receive a burst of lookalike applications.

`config/profile.yaml` is the place to correct notice period, compensation, or any default answer.
