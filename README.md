# Student Task Manager — Flask + MySQL on AWS

Beginner cloud project: a Flask task manager, deployed on an EC2 Linux instance and backed by private Amazon RDS for MySQL.

## Architecture

Browser → HTTP/HTTPS → Nginx on EC2 → Gunicorn/Flask → private RDS MySQL.
GitHub Actions uses GitHub OIDC to assume a narrowly scoped AWS IAM role, then AWS Systems Manager (SSM) to run deployment commands on EC2. No long-lived AWS access key or SSH key is stored in GitHub.

## Features

- User registration and login; Werkzeug password hashing
- Per-user task isolation, create/list/complete/reopen/delete
- MySQL relational schema created by SQLAlchemy on first app startup (see note below)
- Health endpoint at `/health`
- Gunicorn service + Nginx reverse proxy templates
- GitHub Actions deployment through SSM
- RDS automated backups and optional encrypted S3 SQL backups

## Local development

1. Install Python 3.11+, MySQL 8+, and Git.
2. Create a database and a local least-privilege user. In MySQL:
   ```sql
   CREATE DATABASE student_tasks CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
   CREATE USER 'student_app'@'localhost' IDENTIFIED BY 'CHANGE_THIS_LONG_PASSWORD';
   GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, INDEX, ALTER
     ON student_tasks.* TO 'student_app'@'localhost';
   FLUSH PRIVILEGES;
   ```
3. Copy `.env.example` to `.env`, generate a secret and set DB values:
   ```bash
   python -c "import secrets; print(secrets.token_hex(32))"
   ```
   Set `FLASK_SECRET_KEY` to the output and `COOKIE_SECURE=false` only for local HTTP.
4. Install/run:
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   set -a; source .env; set +a
   python -c "from app import app, db; app.app_context().push(); db.create_all()"
   python app.py
   ```
5. Visit http://127.0.0.1:5000

`db.create_all()` creates missing tables but does not migrate an existing schema. For a larger project, use Flask-Migrate/Alembic.

## AWS setup (manual first)

Choose one AWS region and keep EC2/RDS in it. Set a billing budget alert before creating resources. Check current AWS Free Tier eligibility/pricing; don't assume EC2/RDS is free.

### VPC/subnets

For a beginner deployment, use the default VPC only if you understand its subnet routing. Recommended secure layout:
- EC2 app instance in a public subnet (or private subnet behind a load balancer).
- RDS in private subnets in at least two Availability Zones, via a DB subnet group.
- RDS `Public access = No`.
- EC2 has an IAM instance profile with SSM managed-instance permissions and S3 backup permissions (if using optional S3 backups).
- Use SSM Session Manager rather than opening SSH port 22.

### Security groups — exact inbound intent

Create two security groups in the same VPC:

**`task-app-sg` attached to EC2**
- Inbound TCP 80 from `0.0.0.0/0` for a temporary HTTP demo. Prefer HTTPS TCP 443 from `0.0.0.0/0` after TLS is configured.
- Inbound TCP 22: **none** if using SSM. If SSM is not available and you must use SSH, allow TCP 22 only from your current public IP as `x.x.x.x/32`, use a key pair, and remove the rule when done. Never use `0.0.0.0/0` for SSH.
- No inbound 8000 (Gunicorn) or 3306 (MySQL) on EC2.
- Outbound: default allow-all is typical for a learning setup, but restrict in production according to required package, SSM, and database destinations.

**`task-db-sg` attached to RDS**
- Inbound TCP 3306 source = **`task-app-sg` security group ID**, not a public CIDR.
- No inbound 3306 from your laptop or `0.0.0.0/0`.
- Outbound default is usually allowed; RDS does not need a public inbound rule.

Security groups are stateful. Confirm that the RDS rule references the EC2 app SG and that both are in the same VPC.

### RDS MySQL

1. Create RDS → MySQL (choose a supported MySQL 8 version).
2. Set DB identifier `student-tasks-db`, database name `student_tasks`, and a unique master password.
3. For app access, create a separate DB user with only the permissions the app needs. Do not use the master account in Flask.
4. Set Public access to **No** and attach `task-db-sg`.
5. Enable automated backups, retention **7 days** for a demo (use a longer period for important data), and a backup window.
6. Copy the RDS endpoint. It is the `DB_HOST` value, without `https://`.

Example app DB grants (run as DB administrator, replacing password):
```sql
CREATE USER 'student_app'@'%' IDENTIFIED BY 'REPLACE_WITH_LONG_RANDOM_PASSWORD';
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, INDEX, ALTER
ON student_tasks.* TO 'student_app'@'%';
```
Use the narrowest practical host/network restrictions. The `%` host is not a public exposure by itself; network reachability is controlled by RDS and security groups. Avoid granting `DROP`, `GRANT OPTION`, or global privileges to the app.

## EC2 app host

Launch Ubuntu LTS or Amazon Linux. Attach an instance profile that includes:
- `AmazonSSMManagedInstanceCore` (for Session Manager/SSM commands)
- A custom S3 backup policy only if enabling the optional backup script below

No SSH ingress is needed when SSM works. Confirm the instance appears as Online in Systems Manager.

On EC2, use Session Manager to run:

```bash
sudo apt update
sudo apt install -y python3-venv python3-pip git nginx mysql-client
sudo useradd --system --create-home --shell /usr/sbin/nologin taskapp
sudo mkdir -p /opt/student-task-manager /etc/student-task-manager
sudo chown -R taskapp:taskapp /opt/student-task-manager
```

Clone your GitHub repository into `/opt/student-task-manager` (for a public repo):
```bash
sudo -u taskapp git clone https://github.com/YOUR_GITHUB_USER/YOUR_REPO.git /opt/student-task-manager
cd /opt/student-task-manager
sudo -u taskapp python3 -m venv venv
sudo -u taskapp venv/bin/pip install -r requirements.txt
```

Create `/etc/student-task-manager/app.env` with these values (do not commit this file):
```bash
FLASK_SECRET_KEY=GENERATE_A_LONG_RANDOM_SECRET
DB_HOST=YOUR_RDS_ENDPOINT
DB_PORT=3306
DB_NAME=student_tasks
DB_USER=student_app
DB_PASSWORD=YOUR_APP_DB_PASSWORD
COOKIE_SECURE=false
```
Set `COOKIE_SECURE=true` once the site is served over HTTPS. Protect the file:
```bash
sudo chown root:taskapp /etc/student-task-manager/app.env
sudo chmod 640 /etc/student-task-manager/app.env
```

Copy `scripts/student-task-manager.service` to `/etc/systemd/system/student-task-manager.service`, then:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now student-task-manager
sudo systemctl status student-task-manager
```
Initialize tables once:
```bash
sudo -u taskapp bash -c 'cd /opt/student-task-manager && set -a && source /etc/student-task-manager/app.env && set +a && venv/bin/python -c "from app import app, db; app.app_context().push(); db.create_all()"'
```

Configure Nginx: copy `scripts/nginx-student-task-manager.conf` to `/etc/nginx/sites-available/student-task-manager`, replace `YOUR_DOMAIN_OR_EC2_DNS`, symlink it into `sites-enabled`, run `sudo nginx -t`, then `sudo systemctl reload nginx`. Open TCP 80 in the EC2 SG for a temporary demo. For real use, configure HTTPS (for example, a domain + Certbot or an HTTPS load balancer), redirect HTTP to HTTPS, and set `COOKIE_SECURE=true`.

## GitHub Actions deployment with OIDC + SSM

### 1. EC2 prerequisites
- SSM Agent running and instance is Online in Systems Manager.
- EC2 instance role includes `AmazonSSMManagedInstanceCore`.
- App repository is public for the simple `git fetch` workflow below. For a private repo, configure a narrowly scoped deploy credential/token on EC2 (not in source code) or use an artifact-based deployment.
- The `taskapp` user owns the checkout and can fetch `origin/main`.

### 2. Create GitHub OIDC provider and deployment role
In IAM, add GitHub as an OIDC identity provider:
- Provider URL: `https://token.actions.githubusercontent.com`
- Audience: `sts.amazonaws.com`

Create a role such as `github-taskapp-deploy`. Trust policy should restrict both your GitHub account/repository and the `main` branch:
```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {"Federated": "arn:aws:iam::YOUR_ACCOUNT_ID:oidc-provider/token.actions.githubusercontent.com"},
    "Action": "sts:AssumeRoleWithWebIdentity",
    "Condition": {
      "StringEquals": {
        "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
        "token.actions.githubusercontent.com:sub": "repo:YOUR_GITHUB_USER/YOUR_REPO:ref:refs/heads/main"
      }
    }
  }]
}
```
Attach a permissions policy restricted to the one EC2 instance:
```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": "ssm:SendCommand",
    "Resource": [
      "arn:aws:ec2:YOUR_REGION:YOUR_ACCOUNT_ID:instance/YOUR_INSTANCE_ID",
      "arn:aws:ssm:YOUR_REGION::document/AWS-RunShellScript"
    ]
  }, {
    "Effect": "Allow",
    "Action": ["ssm:GetCommandInvocation", "ssm:ListCommandInvocations"],
    "Resource": "*"
  }]
}
```
AWS may require additional SSM resources/permissions for your account and policy validation; test with a least-privilege policy and do not grant `AdministratorAccess`.

### 3. GitHub repository settings
Under Settings → Secrets and variables → Actions:
- Secret `AWS_DEPLOY_ROLE_ARN` = IAM role ARN
- Variable `AWS_REGION` = region such as `ap-south-1`
- Variable `EC2_INSTANCE_ID` = target instance ID

Commit `.github/workflows/deploy.yml`. Push to `main`; check Actions logs. The workflow deploys by SSM, fetches `main`, installs dependencies and restarts systemd. Keep branch protection/review on `main` for a real deployment.

## Automated backups

### Option A — RDS automated backups (recommended baseline)
In RDS → your DB → Modify:
- Automated backups enabled
- Retention 7 days for demo; consider 14–35 days for important data
- Set backup window outside active use
- Enable deletion protection for important DBs
- Take a manual DB snapshot before schema changes or risky deployments

RDS automated backups support point-in-time recovery during the retention window. Periodically test restoring a snapshot to a separate DB and verify the app can connect. A backup that has never been restored is not a proven recovery plan.

### Option B — scheduled SQL dump to S3 (optional extra copy)
Use this as a second backup path, not a replacement for RDS backups. Create an S3 bucket with Block Public Access ON and default encryption ON. Add lifecycle expiration (e.g. 30–90 days, as appropriate).

Attach a custom policy to the EC2 instance role allowing only `s3:PutObject` to `arn:aws:s3:::YOUR_BACKUP_BUCKET/mysql/*` and required multipart actions if needed. Do not put AWS access keys in the script. Use bucket versioning and, if available, Object Lock for stronger protection.

Install `awscli` and `mysqldump` on EC2. Copy `scripts/backup_to_s3.sh` to `/usr/local/bin/backup_to_s3.sh`, make it executable, and ensure its environment file contains `BACKUP_BUCKET=YOUR_BACKUP_BUCKET`. Example cron (daily at 02:15 UTC):
```cron
15 2 * * * root /usr/local/bin/backup_to_s3.sh >> /var/log/taskapp-backup.log 2>&1
```
The script sources `/etc/student-task-manager/app.env`; add `BACKUP_BUCKET` there and ensure the file remains mode 640 and root-owned/group taskapp. For cron to source it, run cron as root (as above); never echo credentials to logs. Consider S3 server-side encryption with KMS for sensitive workloads. Test restore by downloading a backup and importing it into a temporary MySQL database.

## Restore drill (practice this)
1. RDS: restore a snapshot to a new DB instance, or use point-in-time restore to a separate instance.
2. S3 SQL: download the `.sql` object, create a clean temporary database, then import with `mysql`.
3. Verify users/tasks and test the application against the restored endpoint.
4. Delete the temporary restored DB only after recording the result.

## Troubleshooting
- Flask 503 `/health`: check `sudo journalctl -u student-task-manager`, app env file, RDS endpoint, SG source rule, credentials, and RDS status.
- Cannot connect to MySQL: confirm EC2 and RDS are in the same VPC, RDS is private, RDS SG inbound 3306 source is `task-app-sg`, and DB username/password/database name are correct.
- GitHub workflow SSM failure: check OIDC trust `sub`/repo/branch, role policy, EC2 SSM Online status, and instance role.
- If an AWS resource is not eligible for Free Tier, stop/delete it after the exercise and check the billing console.

## Production caveats
This is an educational starter, not a production-ready SaaS. It does not include CSRF protection, rate limiting, email verification, password reset, Alembic migrations, or a full HTTPS provisioning flow. Add CSRF protection (e.g. Flask-WTF), request throttling, HTTPS, security headers, dependency scanning, migrations, and restore testing before real users/data.
