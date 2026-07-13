# Amazon Connect Quota Monitor and Operations Dashboard

**Know which API quota will breach before your next migration wave — not after phones go silent.**

---

## The problem

Amazon Connect enforces rate limits on every API your contact flows call. When you add agents, migrate phone numbers, or launch campaigns, those limits get closer. There's no built-in way to see how close you are, or predict when you'll hit them.

Teams discover throttling after calls start dropping. Quota increases take 3-5 business days. The migration was yesterday.

## What this gives you

Run one command. Get a dashboard that shows:

- How your phone numbers, contact flows, and Lambdas connect to API quotas
- How much headroom you have before hitting limits
- What happens when you add your next batch of agents (migration wave planner)
- Which hour of the day is your peak, and whether it's growing

The dashboard is a self-contained HTML file. No server. Opens offline in any browser. Share it with your team as an email attachment.

---

## Get started (3 commands)

```bash
pip install boto3

python connect-resource-mapper.py \
  --instance-id YOUR_INSTANCE_ID \
  --region us-east-1 \
  --output-dir ./output

open ./output/connect-api-report.html
```

That's it. You'll see your full topology mapped and quota utilization in under 5 minutes.

**Prerequisites:**
- Python 3.9+
- AWS credentials with read-only Connect access ([IAM setup guide](iam/README.md))
- Your Connect Instance ID (find it in your Connect console URL)

---

## What you get

| Output file | Who it's for | What it shows |
|-------------|-------------|---------------|
| `connect-api-report.html` | Operations, engineering | APIs per flow, quota utilization, Lambda inventory, sortable tables |
| `connect-resource-map.json` | Automation | Full topology graph (machine-readable) |
| `connect-quota-impact-model.json` | Planning | Predictive model for migration capacity |

---

## Want it to run automatically?

Deploy the included Lambda. It runs every hour (configurable), generates a fresh HTML dashboard, and uploads it to S3. You get a permanent URL that always shows the latest data.

```bash
cd live-refresh/
sam build
sam deploy --guided
```

After deployment, the output shows your dashboard URL:

```
http://YOUR_BUCKET.s3-website-us-east-1.amazonaws.com/
```

Bookmark it. Every hour the Lambda:
1. Collects current quota utilization from CloudWatch
2. Generates a fresh HTML dashboard
3. Uploads it to S3 (same URL, new data)
4. Saves an archive copy for historical comparison
5. Updates the daily peak file

The dashboard includes a **7-day trend table** that shows peak utilization for each day, so you can see whether you're growing toward a limit.

Details: [Live Refresh setup guide](live-refresh/README.md)

---

## Learn more

| Topic | Guide |
|-------|-------|
| IAM permissions and role setup | [`iam/README.md`](iam/README.md) |
| Lambda deployment and scheduling | [`live-refresh/README.md`](live-refresh/README.md) |
| Secure hosting (CloudFront + corporate SSO) | [`cloudfront-auth/README.md`](cloudfront-auth/README.md) |
| Archives, history, peak tracking | [`docs/operations-guide.md`](docs/operations-guide.md) |
| Configuring business lines | [Business line config](#configuring-business-lines) |
| Architecture and data flow | [Architecture](#architecture) |
| Troubleshooting | [Common issues](#common-issues) |

---

## Configuring business lines

The operations dashboard groups your traffic by business line (Claims, Sales, Service, etc.). Edit `line-config.json`:

```json
{
  "lines": [
    {
      "id": "claims",
      "name": "Claims",
      "tdg_ids": ["8639a7cc-..."],
      "agent_count": 3000
    },
    {
      "id": "sales",
      "name": "Sales",
      "tdg_ids": ["9e209bb8-..."],
      "agent_count": 1400
    }
  ]
}
```

If you skip this file, the dashboard shows all traffic as a single view. You can always add it later.

---

## Architecture

```
You run this:
  python connect-resource-mapper.py --instance-id ... --output-dir ./output

It scans (read-only):
  Phone Numbers → Traffic Distribution Groups → Contact Flows → Lambdas → API Quotas

It generates:
  connect-api-report.html            (interactive, open in browser)
```

For ongoing monitoring, the Lambda does the same thing on a schedule and writes to S3:

```
EventBridge (hourly) → Lambda → CloudWatch + ServiceQuotas
                          │
                          ├→ S3: latest.json (always current)
                          ├→ S3: archive/YYYY-MM-DD/HH-MM.json (each run)
                          └→ S3: peaks/YYYY-MM-DD.json (daily peak)
```

---

## Common issues

| Symptom | Fix |
|---------|-----|
| All quotas show 0% | Run during business hours when calls are active |
| "Access Denied" errors | Check your IAM policy — see [`iam/README.md`](iam/README.md) |
| Scan takes > 5 minutes | Normal for large instances (50K+ numbers) |
| Dashboard shows "No data" | Check output directory exists and re-run |

---

## License

MIT-0 (No attribution required). See [LICENSE](LICENSE).
