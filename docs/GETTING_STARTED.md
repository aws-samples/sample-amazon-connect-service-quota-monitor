# Local report generation (CLI only, no deployment)

This is an optional local tool. It is not the way to deploy the Connect Quota Monitor solution. For deployment, see the root [README.md](../README.md) and [terraform/README.md](../terraform/README.md), which set up the scheduled Lambda functions, the alerting, and the live-refresh dashboard through Terraform.

This guide walks you through running `connect-resource-mapper.py` on your own machine to produce a one-off, static HTML report and dashboard from your Connect instance's current configuration. There is no deployment, no schedule, and no login: the output is a set of local files you open in a browser once. Follow it exactly. If something looks different from what's described, check the Common Issues section in the README.

---

## Install Python

<details>
<summary><strong>macOS</strong></summary>

Python 3 comes pre-installed on macOS 12.3+. Open **Terminal** (search for it in Spotlight) and type:
```bash
python3 --version
```

You should see something like `Python 3.12.4`. If it says `command not found` or shows a version below 3.9:
```bash
brew install python@3.12
```

Don't have Homebrew? Install it first by pasting this into Terminal:
```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```
Then try `brew install python@3.12` again.
</details>

<details>
<summary><strong>Windows</strong></summary>

1. Go to https://www.python.org/downloads/
2. Click the big yellow "Download Python 3.12.x" button
3. Run the installer
4. **IMPORTANT:** Check the box that says "Add Python to PATH" at the bottom of the first screen
5. Click "Install Now"

Verify it worked. Open **PowerShell** (search for it in Start menu) and type:
```powershell
python --version
```

You should see `Python 3.12.x`. If it says `command not found`, restart PowerShell and try again.
</details>

<details>
<summary><strong>Linux (Ubuntu / Amazon Linux)</strong></summary>

```bash
# Ubuntu / Debian
sudo apt update && sudo apt install python3 python3-pip python3-venv

# Amazon Linux 2023
sudo dnf install python3.12 python3.12-pip
```

Verify:
```bash
python3 --version
```
</details>

---

## Install AWS CLI and configure credentials

<details>
<summary><strong>macOS</strong></summary>

```bash
brew install awscli
```

Or download the installer: https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html
</details>

<details>
<summary><strong>Windows</strong></summary>

Download and run the MSI installer: https://awscli.amazonaws.com/AWSCLIV2.msi

Restart PowerShell after installing.
</details>

<details>
<summary><strong>Linux</strong></summary>

```bash
curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip"
unzip awscliv2.zip
sudo ./aws/install
```
</details>

### Configure your credentials

After installing the CLI, configure it with your AWS access keys:

```bash
aws configure
```

It will ask for:
- **AWS Access Key ID:** Get this from IAM console → Users → Your user → Security credentials
- **AWS Secret Access Key:** Same place (you only see this once when you create it)
- **Default region:** The region where your Connect instance lives (e.g., `us-east-1`)
- **Default output format:** Just press Enter (defaults to `json`)

**Using SSO / Identity Center instead?**
```bash
aws configure sso
```

**Verify it works:**
```bash
aws sts get-caller-identity
```

You should see your account number. If you get "expired" or "access denied", re-run `aws configure`.

The IAM permissions needed: [`iam/README.md`](../iam/README.md)

---

## Find your Connect Instance ID

### Option A: From the console

1. Go to https://console.aws.amazon.com/connect/
2. Make sure you're in the correct region (top-right corner)
3. Click on your instance alias (the name you gave it)
4. Look at the URL in your browser. It contains your Instance ID:

```
https://console.aws.amazon.com/connect/home?region=us-east-1#/instance/YOUR_INSTANCE_ID/dashboard
                                                                         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
                                                                         This is your Instance ID
```

### Option B: From the CLI

```bash
aws connect list-instances --region us-east-1
```

Look for the `"Id"` field in the output.

**Write it down.** You'll need it in the next step.

---

## Download and run

### Step 1: Download the code

<details>
<summary><strong>macOS / Linux</strong></summary>

```bash
git clone https://github.com/aws-samples/sample-amazon-connect-service-quota-monitor.git
cd sample-amazon-connect-service-quota-monitor
```

`git` not installed? Run `brew install git` (macOS) or `sudo apt install git` (Linux).
</details>

<details>
<summary><strong>Windows</strong></summary>

```powershell
git clone https://github.com/aws-samples/sample-amazon-connect-service-quota-monitor.git
cd sample-amazon-connect-service-quota-monitor
```

`git` not installed? Download from https://git-scm.com/download/win, then restart PowerShell after installing.
</details>

### Step 2: Create a virtual environment and install dependencies

<details>
<summary><strong>macOS / Linux</strong></summary>

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

**How you know it worked:** Your terminal prompt now starts with `(.venv)`.
</details>

<details>
<summary><strong>Windows (PowerShell)</strong></summary>

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

**How you know it worked:** Your prompt now starts with `(.venv)`.

> **Error: "running scripts is disabled"?** Run this first, then try again:
> ```powershell
> Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
> ```
</details>

### Step 3: Run the resource mapper

Now run the tool. **Replace the instance ID and region with yours:**

<details>
<summary><strong>macOS / Linux</strong></summary>

```bash
python3 connect-resource-mapper.py \
  --instance-id YOUR_INSTANCE_ID \
  --region us-east-1 \
  --output-dir ./output
```
</details>

<details>
<summary><strong>Windows (PowerShell)</strong></summary>

```powershell
python connect-resource-mapper.py `
  --instance-id YOUR_INSTANCE_ID `
  --region us-east-1 `
  --output-dir ./output
```
</details>

**What success looks like:**
```
────────────────────────────────────────────────────────────
Outputs:
  ./output/connect-resource-map.json
  ./output/connect-quota-impact-model.json
  ./output/connect-dashboard.html  ← Open in browser
  ./output/connect-api-report.html  ← Consolidated API Report
────────────────────────────────────────────────────────────
Summary:
  Phone numbers: 12
  Contact flows: 25
  Lambdas: 8
  Provisioned Concurrency: 2
  Quotas > 70% utilized: 1
════════════════════════════════════════════════════════════
```

You get four files: two JSON data files, and two self-contained HTML views
(`connect-dashboard.html` and `connect-api-report.html`). The numbers above are
an example; yours reflect your own instance.

**What failure looks like and what to do:**

| You see | Problem | Fix |
|---------|---------|-----|
| `AccessDeniedException` | Your IAM user doesn't have Connect permissions | See [iam/README.md](../iam/README.md) |
| `InvalidParameterException` | Wrong instance ID format | Double-check the ID (should be UUID format: `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`) |
| `Connect instance not found` | Wrong region | Make sure `--region` matches where your instance lives |
| `No module named 'boto3'` | Dependencies not installed | Run `pip install -r requirements.txt` again (make sure venv is active) |

### Step 4: Open the dashboard

<details>
<summary><strong>macOS</strong></summary>

```bash
open ./output/connect-api-report.html
```
</details>

<details>
<summary><strong>Windows</strong></summary>

```powershell
start .\output\connect-api-report.html
```
</details>

<details>
<summary><strong>Linux</strong></summary>

```bash
xdg-open ./output/connect-api-report.html
```
</details>

Your browser opens with the dashboard. You should see:

- **Top section:** API quota summary with utilization bars (green = safe, yellow = watch, red = critical)
- **Middle:** Per-flow breakdown showing which flows call which APIs
- **Bottom:** Lambda inventory and migration impact calculator

> **Dashboard shows all zeros?** That's normal if you run outside business hours. The tool reads CloudWatch metrics which only have data when calls are active. Re-run during peak hours for real utilization data.

---

## Next steps

- **Want it to refresh automatically?** Deploy the Lambda: see [live-refresh/README.md](../live-refresh/README.md)
- **Want to group by business line?** Configure the Configuring Business Lines section in `line-config.json`

---

## Windows instructions

All commands in this guide have Windows (PowerShell) equivalents in collapsible sections. Key differences:

| macOS/Linux | Windows (PowerShell) |
|-------------|---------------------|
| `python3` | `python` |
| `source .venv/bin/activate` | `.venv\Scripts\Activate.ps1` |
| Line continuation: `\` | Line continuation: `` ` `` |
| `open file.html` | `start file.html` |
| `/` in paths | `\` in paths |
