# Operations Guide

Detailed reference for running and maintaining the Connect Quota Dashboard.

## How frequently does it run?

| Mode | Frequency | Where |
|------|-----------|-------|
| CLI (you run it) | On-demand | Local filesystem |
| Lambda (automated) | Every hour (configurable) | S3 bucket |

Change the schedule in `live-refresh/template.yaml`:

```yaml
Schedule: rate(1 hour)      # Steady state
Schedule: rate(15 minutes)  # During migrations (high risk)
Schedule: rate(4 hours)     # Low activity periods
```

## Same URL, always fresh

The Lambda overwrites `latest.json` on every run. Point your browser or bookmark at:

```
http://YOUR_BUCKET.s3-website-us-east-1.amazonaws.com/dashboard/index.html
```

Or put CloudFront in front for HTTPS.

## Archives and history

Every run writes two things:

1. **Hourly snapshot** → `archive/YYYY-MM-DD/HH-MM.json`
2. **Daily peak update** → `peaks/YYYY-MM-DD.json` (only updates if this hour exceeds today's previous peak)

### Looking back in time

The Lambda API supports history queries:

```
GET /quota                → latest snapshot
GET /quota?history=1h     → last hour (minute-by-minute)
GET /quota?history=1d     → last 24 hours
GET /quota?history=7d     → last 7 days (daily peaks)
GET /quota?history=trend  → last 30 days (daily peaks)
```

### Retention

Set S3 Lifecycle rules:
- `archive/` — 90 days, then delete
- `peaks/` — 365 days (year-over-year comparison)
- `dashboard/` — never expires

## Peak tracking

Each hourly run:
1. Queries CloudWatch for ConcurrentCalls, ConcurrentChats, ConcurrentTasks
2. Queries per-API TPS (GetContactAttributes, etc.)
3. Compares against quota limits
4. If this hour exceeds today's previous peak, updates the peak file

The dashboard uses peak files to show:
- Which hour is consistently the peak
- Whether peaks are growing week-over-week
- How much headroom shrinks as you scale

## Deployment options

| Option | Complexity | Best for |
|--------|-----------|----------|
| CLI only | Low | One-time assessment, pre-migration planning |
| Lambda + S3 | Medium | Ongoing monitoring, shared dashboard |
| Lambda + API Gateway + CloudFront | Medium-High | NOC screen, real-time ops center |

## Accessibility

Dashboard HTML complies with WCAG 2.1 AA:

- Color + shape indicators (not color-only)
- Keyboard navigation (Tab, Enter, Arrow keys)
- Screen reader labels (ARIA)
- Respects browser zoom (rem-based sizing)
- Reduced motion support
- Colorblind palette (`?colorblind` URL param)
- Print stylesheet included

## Verifying your deployment

**Step 1:** Run the CLI mapper and open the dashboard in your browser.

**Step 2:** Invoke the Lambda and check for `statusCode: 200`:
```bash
aws lambda invoke --function-name connect-quota-dashboard \
  --cli-binary-format raw-in-base64-out \
  --payload '{"queryStringParameters": {}}' output.json
```

**Step 3:** After one hour, verify archive files exist:
```bash
aws s3 ls s3://YOUR_BUCKET/archive/
aws s3 ls s3://YOUR_BUCKET/peaks/
```
