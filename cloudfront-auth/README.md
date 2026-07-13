# Secure Dashboard Hosting (CloudFront + Corporate SSO)

Users log in with their corporate credentials (Okta, Azure AD, Ping, OneLogin, or any SAML 2.0 provider) before seeing the dashboard. No extra passwords. No public URLs.

## How it works

```
User opens dashboard URL
    → CloudFront checks for session cookie
    → No cookie? Redirect to corporate SSO login page
    → User authenticates with their corporate identity
    → Redirect back with session cookie
    → CloudFront serves dashboard from private S3 bucket
```

## Deploy (5 minutes)

### Step 1: Get your SAML metadata URL

Ask your identity team for the SAML metadata URL. It looks like:
- Okta: `https://your-company.okta.com/app/xxxxx/sso/saml/metadata`
- Azure AD: `https://login.microsoftonline.com/TENANT_ID/federationmetadata/2007-06/federationmetadata.xml`
- Ping: `https://sso.your-company.com/saml/metadata`

### Step 2: Deploy the stack

```bash
cd cloudfront-auth/
sam build
sam deploy --guided
```

It will ask for:
- `DomainPrefix` — a unique name for your login page (e.g., `allstate-connect-dashboard`)
- `SAMLMetadataURL` — the URL from Step 1
- `DashboardBucketName` — a unique S3 bucket name

### Step 3: Configure your identity provider

After deployment, the outputs show two values your identity team needs:

- **Entity ID**: `urn:amazon:cognito:sp:YOUR_POOL_ID`
- **ACS URL**: `https://YOUR_DOMAIN.auth.REGION.amazoncognito.com/saml2/idpresponse`

Give these to your identity team. They create a SAML app and map the `email` attribute.

### Step 4: Upload the dashboard

```bash
# Generate fresh dashboard
python connect-resource-mapper.py --instance-id YOUR_ID --region us-east-1 --output-dir ./output

# Upload to the private S3 bucket
aws s3 sync ./output/ s3://YOUR_BUCKET/ --content-type "text/html"
```

### Step 5: Open the dashboard

Go to the URL from the deployment output (something like `https://d1234567.cloudfront.net/`). You'll be redirected to your corporate login. After authenticating, the dashboard loads.

## Automated refresh

Combine with the live-refresh Lambda to keep the dashboard fresh:

1. Deploy the live-refresh Lambda (see `live-refresh/README.md`)
2. Set `S3_BUCKET` to the same bucket name from this stack
3. The Lambda updates the dashboard hourly. CloudFront serves the latest version.

Users bookmark one URL. Data refreshes automatically. Auth handles itself.

## Security

- S3 bucket is private. No public access.
- Only CloudFront can read from S3 (Origin Access Control).
- Users must authenticate via SAML before seeing any content.
- Session cookie expires after 8 hours (configurable in Lambda@Edge code).
- HTTPS only (HTTP redirects to HTTPS).
- No credentials stored in the dashboard HTML.

## Works with

- Okta
- Azure Active Directory
- Ping Identity
- OneLogin
- Any SAML 2.0 compliant identity provider
