# Lambda Labs Instance Manager

A CLI tool to create and manage Lambda Labs GPU instances programmatically.

## Features

- **Create instances** - Launch GPU instances with automatic cheapest selection
- **Delete instances** - Terminate instances by ID
- **List instances** - View all active instances with uptime and cost tracking
- **Show instance types** - Browse available and unavailable GPU instance types
- **Restart instances** - Restart running instances

## Lambda Cloud API Endpoint

The API endpoint is set to: `https://cloud.lambda.ai/api/v1`

You can modify the `API_BASE_URL` in `lambda_manager.py` if needed:
```python
API_BASE_URL = "https://your-custom-endpoint.com/v1"
```

## Installation

```bash
pip install -r requirements.txt
```

## Usage

Set your Lambda Labs API key and default SSH key as environment variables:
```bash
export LAMBDA_API_KEY="your_api_key_here"
export LAMBDA_DEFAULT_SSH_KEY="your_ssh_key_name"
```

### Create the Cheapest Instance with Default SSH Key
```bash
python lambda_manager.py create
```
This will automatically find the cheapest available GPU instance type with capacity.

**Expected Prices:**
- **H100**: ~$3.25-3.50/hour
- **A100**: ~$1.50-1.75/hour
- **H200**: ~$4.00-4.50/hour
- **B200**: Starting from $5.00/hour
*Prices vary by region and availability*

### Create with Type Filter
```bash
# Filter to specific instance types
python lambda_manager.py create \
  --type-filter "a100,h100" \
  --region us-east-1 \
  --key my_ssh_key \
  --count 1
```

### Create with Custom Name
```bash
python lambda_manager.py create \
  --type-filter "h100" \
  --name "my-gpu-instance" \
  --key my_ssh_key
```

### Wait for Instance to Start
```bash
# Launch and wait for the instance to be reachable via ping
python lambda_manager.py create --wait

# Launch with type filter and wait
python lambda_manager.py create --type-filter "a100,h100" --wait
```

The `--wait` flag will poll the instance IP every 10 seconds until it responds to ping (max 20 minutes).

### Delete an Instance
```bash
# Delete by specific instance ID
python lambda_manager.py delete instance-id-12345

# First find your instance ID:
python lambda_manager.py list
```

Note: Instance termination is processed asynchronously. Use `list` to verify deletion.

### List All Running Instances
```bash
# Display formatted output
python lambda_manager.py list

# Get JSON output
python lambda_manager.py list --output json
```

Output shows for each instance:
- **Instance ID**
- **Instance name**
- **Status** (running, stopped, or other)
- **Instance type** (e.g., gpu_1x_h100_sxm5)
- **Price per hour** (e.g., $3.25/hr)
- **CPU cores, Memory (GiB), Storage (GiB), GPU count**
- **Public IP address**
- **Uptime via SSH** (e.g., "up 2 hours, 15 minutes")
- **Runtime** (calculated total hours, e.g., "2.25 hours")
- **Cost** for this instance (price × runtime, e.g., "$7.31")

At the end of the list, you'll see summary information:
- **Total hourly cost** for all instances combined
- **Total running cost** accumulated across all instances

**Note:** Uptime is fetched via SSH using the `uptime -p` command. This requires SSH key access configured with the Lambda instance. The Lambda API doesn't natively expose instance runtime, so SSH is used to get this information and calculate total running costs.

### Show Available Instance Types
```bash
# Display formatted output
python lambda_manager.py instances

# Get JSON output
python lambda_manager.py instances --output json
```

Output shows all instance types separated by:
- Available types (with capacity) — includes price per hour
- Unavailable types (no current capacity)

### Restart an Instance
```bash
# Restart a specific instance
python lambda_manager.py restart instance-id-12345

# Get JSON output
python lambda_manager.py restart instance-id-12345 --output json
```

### Launch Multiple Instances
```bash
# Launch 3 instances with different keys
python lambda_manager.py create \
  --type-filter "a100" \
  --count 3 \
  --key my_key_1 \
  --key my_key_2
```

### Select Specific Region
```bash
python lambda_manager.py create \
  --type-filter "a100" \
  --region us-east-1 \
  --key my_ssh_key
```

### Show Connection Information
After launching an instance with `create`, the tool automatically displays:
- Instance ID
- Status
- Public IP address (for SSH connections)
- Jupyter URL (if jupyter is running)
- SSH command (ready to copy-paste)

**Example SSH connection:**
```bash
ssh -o StrictHostKeyChecking=no ubuntu@<instance-ip>
```

### Get JSON Output for Programmatic Use
```bash
python lambda_manager.py create --output json
python lambda_manager.py list --output json
python lambda_manager.py instances --output json
python lambda_manager.py restart instance-id --output json
```

## Environment Variables

| Variable | Description |
|----------|-------------|
| `LAMBDA_API_KEY` | Your Lambda Labs API key (required for all operations) |
| `LAMBDA_DEFAULT_SSH_KEY` | Default SSH key name to use for instance creation |

**Note:** Command-line flags (`--api-key`, `--key`) will override these environment variables.

## Error Handling

The tool provides clear error messages for:
- Missing or invalid API key
- Network issues
- Invalid instance types
- Region availability issues
- API rate limits

## License

MIT License - Feel free to use and modify as needed.