# Getting Started — Full Setup Guide

This guide walks you through every step from zero to a working dashboard. Follow it exactly — if something looks different from what's described, check [Troubleshooting](TROUBLESHOOTING.md).

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

Verify it worked — open **PowerShell** (search for it in Start menu) and type:
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
4. Look at the URL in your browser — it contains your Instance ID:

```
https://console.aws.amazon.com/connect/home?region=us-east-1#/instance/6c3f17c0-3b52-4990-9c42-e27dd792b385/dashboard
                                                                         ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
                                                                         This is your Instance ID
```

### Option B: From the CLI

```bash
aws connect list-instances --region us-east-1
```

Look for the `"Id"` field in the output.

**Write it down** — you'll need it in the next step.

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

`git` not installed? Download from https://git-scm.com/download/win — restart PowerShell after installing.
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
  --instance-id 6c3f17c0-3b52-4990-9c42-e27dd792b385 \
  --region us-east-1 \
  --output-dir ./output
```
</details>

<details>
<summary><strong>Windows (PowerShell)</strong></summary>

```powershell
python connect-resource-mapper.py `
  --instance-id 6c3f17c0-3b52-4990-9c42-e27dd792b385 `
  --region us-east-1 `
  --output-dir ./output
```
</details>

**What success looks like:**
```
[INFO] Scanning instance 6c3f17c0-3b52-4990-...
[INFO] Found 12 phone numbers
[INFO] Found 3 traffic distribution groups
[INFO] Found 25 contact flows
[INFO] Found 8 Lambda functions
[INFO] Quota impact model generated
[INFO] Dashboard written to ./output/connect-api-report.html
✓ Complete — 3 files written to ./output/
```

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

- **Want it to refresh automatically?** Deploy the Lambda → [live-refresh/README.md](../live-refresh/README.md)
- **Need secure hosting with SSO?** Set up CloudFront + Cognito → [cloudfront-auth/README.md](../cloudfront-auth/README.md)
- **Want to group by business line?** Configure line-config.json → [BUSINESS_LINES.md](BUSINESS_LINES.md)

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
