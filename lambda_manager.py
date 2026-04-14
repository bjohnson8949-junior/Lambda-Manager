#!/usr/bin/env python3
"""
Lambda Labs Instance Manager CLI

A CLI tool to create and delete Lambda Labs GPU instances programmatically.
"""

import argparse
import json
import os
import sys
from typing import Optional, Union, List

import requests


class LambdaAPIError(Exception):
    """Custom exception for Lambda API errors."""
    pass


class LambdaInstanceManager:
    """Manages Lambda Labs GPU instances via their API."""

    API_BASE_URL = "https://cloud.lambda.ai/api/v1"

    def __init__(self, api_key: Optional[str] = None, test_mode: bool = False, default_ssh_key: Optional[str] = None):
        """
        Initialize the Lambda Instance Manager.

        Args:
            api_key: Lambda Labs API key. If not provided, reads from LAMBDA_API_KEY env var.
            test_mode: If True, doesn't connect to real API (for testing).
            default_ssh_key: Default SSH key name to use for new instances.
        """
        self.test_mode = test_mode
        self.default_ssh_key = default_ssh_key or os.environ.get("LAMBDA_DEFAULT_SSH_KEY")
        if not test_mode:
            self.api_key = api_key or os.environ.get("LAMBDA_API_KEY")
            if not self.api_key:
                raise ValueError(
                    "API key not provided. Set LAMBDA_API_KEY environment variable "
                    "or pass api_key parameter."
                )
            self.headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            }

    def _api_request(self, method: str, endpoint: str, data: Optional[dict] = None) -> dict:
        """
        Make a request to the Lambda API.

        Args:
            method: HTTP method (GET, POST, DELETE)
            endpoint: API endpoint path
            data: Request body data

        Returns:
            JSON response as dictionary
        """
        url = f"{self.API_BASE_URL}{endpoint}"
        response = None
        try:
            if method == "GET":
                response = requests.get(url, headers=self.headers, timeout=(10, 60))
            elif method == "POST":
                response = requests.post(
                    url, headers=self.headers, json=data, timeout=(10, 60)
                )
            elif method == "DELETE":
                response = requests.delete(url, headers=self.headers, timeout=(10, 60))
            else:
                raise ValueError(f"Unsupported method: {method}")

            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            error_msg = f"API request failed: {e}"
            if response is not None:
                try:
                    if response.text:
                        error_msg += f"\nResponse: {response.text}"
                except Exception:
                    pass
            raise LambdaAPIError(error_msg)

    def get_instance_types(self) -> dict:
        """Get available instance types."""
        response = self._api_request("GET", "/instance-types")
        return response.get("data", response)

    def get_instances(self) -> dict:
        """Get all instances associated with the account."""
        response = self._api_request("GET", "/instances")
        if not isinstance(response, dict):
            raise LambdaAPIError("Invalid API response: expected dict")

        data = response.get("data", response)
        # Convert list to dict for compatibility
        if isinstance(data, list):
            return {inst["id"]: inst for inst in data if isinstance(inst, dict) and "id" in inst}
        elif isinstance(data, dict):
            return data
        else:
            return {}

    def get_instance(self, instance_id: str) -> dict:
        """Get details of a specific instance."""
        if not instance_id or not instance_id.strip():
            raise ValueError("Instance ID cannot be empty")
        response = self._api_request("GET", f"/instances/{instance_id}")
        if not isinstance(response, dict):
            raise LambdaAPIError("Invalid API response: expected dict")
        return response

    def find_cheapest_instance(self, instance_type_filter: Optional[set] = None) -> tuple[str, float]:
        """
        Find the cheapest available instance type.

        Args:
            instance_type_filter: Set of allowed instance type names to filter by.

        Returns:
            Tuple of (instance_type_name, price_per_hour)
        """
        instance_types_response = self.get_instance_types()

        if not instance_types_response:
            raise LambdaAPIError("No instance types available")

        # Extract instance types from response
        # Handle both response formats: {"data": {...}} or {"data": [...]}
        instance_types = instance_types_response.get("data", instance_types_response)

        # If data is a list, convert to dict
        if isinstance(instance_types, list):
            instance_types = {it.get("instance_type", {}).get("name", f"instance_{i}"): it
                            for i, it in enumerate(instance_types)}
        elif isinstance(instance_types, dict):
            # API returns {"instance_type_id": {"instance_type": {...}, "regions_with_capacity_available": [...]}}
            # Convert to flat dict with price info
            converted_types = {}
            for key, value in instance_types.items():
                if isinstance(value, dict):
                    inst_info = value.get("instance_type", value)
                    if isinstance(inst_info, dict):
                        inst_info["regions"] = value.get("regions_with_capacity_available", [])
                        converted_types[key] = inst_info
                    else:
                        converted_types[key] = value
                else:
                    converted_types[key] = value
            instance_types = converted_types

        if not instance_types:
            raise LambdaAPIError("No instance types available")

        # Filter for GPU instances and find the cheapest with available capacity
        cheapest = None
        cheapest_price = float("inf")

        for instance_type_id, instance_type in instance_types.items():
            if not isinstance(instance_type, dict):
                continue

            # Apply instance type filter if provided
            # Support both full type names (gpu_1x_h100_sxm5) and partial matches (h100, a100)
            if instance_type_filter:
                # Check if full name matches
                if instance_type_id in instance_type_filter:
                    pass  # Exact match
                else:
                    # Check for partial match - e.g., filter "h100" matches "gpu_1x_h100_sxm5"
                    matched = False
                    for filter_term in instance_type_filter:
                        if filter_term.lower() in instance_type_id.lower():
                            matched = True
                            break
                    if not matched:
                        continue

            price_per_hour = instance_type.get("price_cents_per_hour", 0) / 100.0
            gpu_description = instance_type.get("gpu_description", "")

            # Only consider instances with GPU in description
            if "GPU" in gpu_description.upper() or any(
                term in gpu_description.lower() for term in ["h100", "a100", "b200", "gh200", "v100", "a6000", "a10"]
            ):
                # Check regions - API stores capacity info in "regions" key (added during conversion)
                # Empty list means NO capacity, non-empty means HAS capacity
                regions = instance_type.get("regions", instance_type.get("regions_with_capacity_available", []))
                has_capacity = len(regions) > 0
                if has_capacity and price_per_hour < cheapest_price:
                    cheapest = instance_type_id
                    cheapest_price = price_per_hour

        if cheapest is None:
            # No instances have capacity - raise an error
            raise LambdaAPIError("No GPU instances available in any region")

        return cheapest, cheapest_price

    def launch_instance(
        self,
        instance_type: Optional[str] = None,
        region: Optional[str] = None,
        ssh_key_names: Optional[list] = None,
        quantity: int = 1,
        name: Optional[str] = None,
        instance_type_filter: Optional[Union[set, List[str]]] = None,
    ) -> dict:
        """
        Launch a new Lambda instance.

        Args:
            instance_type: Type of instance to launch. If None, uses cheapest.
            region: Region to launch in. If None, uses region with cheapest instance.
            ssh_key_names: List of SSH key names to add.
            quantity: Number of instances to launch.
            name: Name for the instance.
            instance_type_filter: Set or list of allowed instance type names to filter by.

        Returns:
            Instance details including instance ID.
        """
        # Validate inputs
        if quantity < 1:
            raise ValueError("Quantity must be at least 1")
        if quantity > 10:
            print("Warning: Lambda may limit the number of concurrent instances")

        # Parse instance type filter if provided (can be set, list, or comma-separated string)
        allowed_types = None
        if instance_type_filter:
            if isinstance(instance_type_filter, str):
                allowed_types = {t.strip() for t in instance_type_filter.split(",") if t.strip()}
            elif isinstance(instance_type_filter, list):
                allowed_types = {t for t in instance_type_filter if t}
            elif isinstance(instance_type_filter, set):
                allowed_types = instance_type_filter

        # If no region specified, try to find one with capacity
        if region is None:
            # Resolve partial type names to full names if needed
            if instance_type and not instance_type.startswith("gpu_") and not instance_type.startswith("cpu_"):
                # This is a partial match like "a100" or "h100" - need to find the full name
                instance_types_response = self.get_instance_types()
                types_data = instance_types_response.get("data", instance_types_response)
                if isinstance(types_data, dict):
                    for full_name in types_data.keys():
                        if instance_type.lower() in full_name.lower():
                            instance_type = full_name
                            print(f"Resolved type to: {instance_type}")
                            break

            # Cache instance types to avoid redundant API calls
            if 'instance_types_response' not in locals():
                instance_types_response = self.get_instance_types()

            if instance_types_response:
                types_data = instance_types_response.get("data", instance_types_response)
                if isinstance(types_data, dict):
                    # Find the cheapest instance type first
                    if instance_type is None:
                        instance_type, _ = self.find_cheapest_instance(instance_type_filter=allowed_types)
                        print(f"Selected cheapest instance type: {instance_type}")

                    # Get the info for this instance type
                    inst_type_info = types_data.get(instance_type, {})
                    if isinstance(inst_type_info, dict):
                        regions = inst_type_info.get("regions_with_capacity_available", [])
                        if isinstance(regions, list) and len(regions) > 0:
                            region = regions[0].get("name", "")
                            if region:
                                print(f"Found capacity in region: {region}")

                    # If still no region, find any region with capacity
                    if not region:
                        for key, value in types_data.items():
                            if isinstance(value, dict):
                                regions = value.get("regions_with_capacity_available", [])
                                if isinstance(regions, list) and len(regions) > 0:
                                    region = regions[0].get("name", "")
                                    if region:
                                        print(f"Using region from available capacity: {region}")
                                        break
                                if region:
                                    break

            # Final fallback
            if not region:
                region = "us-east-1"
                print(f"Using default region: {region}")

        if allowed_types:
            print(f"Filtering instance types to: {', '.join(sorted(allowed_types))}")

        # Use default SSH key if no keys provided and user didn't specify any
        if not ssh_key_names and self.default_ssh_key:
            ssh_key_names = [self.default_ssh_key]

        launch_data = {
            "instance_type_name": instance_type,
            "ssh_key_names": ssh_key_names if ssh_key_names else [],
            "quantity": quantity,
        }

        if region:
            launch_data["region_name"] = region
        if name:
            launch_data["name"] = name

        print(f"Launching {quantity} {instance_type} instance(s) in {region}...")
        response = self._api_request("POST", "/instance-operations/launch", launch_data)

        print("Instance launched successfully!")
        return response

    def delete_instance(self, instance_id: str) -> bool:
        """
        Delete/terminate a Lambda instance.

        Args:
            instance_id: ID of the instance to delete.

        Returns:
            True if deletion was successful.
        """
        if not instance_id or not instance_id.strip():
            raise ValueError("Instance ID cannot be empty")
        print(f"Terminating instance: {instance_id}...")
        response = self._api_request("POST", "/instance-operations/terminate", {"instance_ids": [instance_id]})
        if isinstance(response, dict) and "data" in response:
            data = response.get("data", {})
            terminated = data.get("terminated_instances", [])
            if any(inst.get("id") == instance_id for inst in terminated):
                print("Instance termination request accepted!")
                return True
        print("Instance termination request accepted!")
        return True

    def restart_instance(self, instance_id: str) -> dict:
        """
        Restart a Lambda instance.

        Args:
            instance_id: ID of the instance to restart.

        Returns:
            Instance details after restart.
        """
        if not instance_id or not instance_id.strip():
            raise ValueError("Instance ID cannot be empty")
        print(f"Restarting instance: {instance_id}...")
        # Lambda Cloud API restarts the instance via the instance-operations endpoint
        response = self._api_request("POST", "/instance-operations/restart", {"instance_ids": [instance_id]})
        if isinstance(response, dict) and "data" in response:
            data = response.get("data", {})
            restarted = data.get("restarted_instances", [])
            if any(inst.get("id") == instance_id for inst in restarted):
                print("Instance restarted successfully!")
                return restarted[0] if restarted else response
        print("Instance restart request submitted!")
        return response

    def start_instance(self, instance_id: str) -> dict:
        """
        Start a Lambda instance.
        Note: This calls restart for instances that need it.

        Args:
            instance_id: ID of the instance to start.

        Returns:
            Instance details after starting.
        """
        if not instance_id or not instance_id.strip():
            raise ValueError("Instance ID cannot be empty")
        print(f"Starting instance: {instance_id}...")
        response = self._api_request("POST", "/instance-operations/restart", {"instance_ids": [instance_id]})
        print("Instance start/restart request submitted!")
        return response

    def stop_instance(self, instance_id: str) -> dict:
        """
        Stop a Lambda instance.

        Args:
            instance_id: ID of the instance to stop.

        Returns:
            Instance details after stopping.
        """
        if not instance_id or not instance_id.strip():
            raise ValueError("Instance ID cannot be empty")
        print(f"Stopping instance: {instance_id}...")
        print("Warning: Lambda API does not have a direct stop action.")
        print("Use 'terminate' to delete or 'restart' to restart the instance.")
        return {}

    def healthcheck_instance(self, instance_id: str) -> dict:
        """
        Check health status of a Lambda instance.

        Args:
            instance_id: ID of the instance to check.

        Returns:
            Instance status details.
        """
        if not instance_id or not instance_id.strip():
            raise ValueError("Instance ID cannot be empty")
        response = self.get_instance(instance_id)
        # Handle both wrapped and unwrapped responses
        instance = response.get("data", response) if isinstance(response, dict) else response
        status = instance.get("status", "unknown") if isinstance(instance, dict) else "unknown"
        ip = instance.get("ip", instance.get("private_ip", "N/A")) if isinstance(instance, dict) else "N/A"

        print(f"Instance {instance_id} Health Status:")
        print(f"  Status: {status}")
        print(f"  IP: {ip}")

        return instance


def parse_uptime_to_hours(uptime_str: str) -> float:
    """
    Parse uptime string from 'uptime -p' to total hours.

    Example: "up 1 week, 5 days, 3 hours, 22 minutes" -> ~183.37 hours
    """
    import re

    if not uptime_str or not uptime_str.startswith("up "):
        return 0.0

    # Remove "up " prefix
    uptime_str = uptime_str[3:].strip()

    hours = 0.0

    # Extract weeks
    weeks_match = re.search(r'(\d+)\s*week', uptime_str, re.IGNORECASE)
    if weeks_match:
        hours += int(weeks_match.group(1)) * 7 * 24

    # Extract days
    days_match = re.search(r'(\d+)\s*day', uptime_str, re.IGNORECASE)
    if days_match:
        hours += int(days_match.group(1)) * 24

    # Extract hours
    hours_match = re.search(r'(\d+)\s*hour', uptime_str, re.IGNORECASE)
    if hours_match:
        hours += int(hours_match.group(1))

    # Extract minutes
    minutes_match = re.search(r'(\d+)\s*minute', uptime_str, re.IGNORECASE)
    if minutes_match:
        hours += int(minutes_match.group(1)) / 60.0

    return round(hours, 2)


def main():
    """Main entry point for the CLI."""
    parser = argparse.ArgumentParser(
        description="Manage Lambda Labs GPU instances"
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Create instance command
    create_parser = subparsers.add_parser(
        "create", help="Create (launch) a Lambda instance"
    )
    create_parser.add_argument(
        "--region",
        "-r",
        help="Region to launch in (default: where cheapest instance is available)",
    )
    create_parser.add_argument(
        "--key",
        "-k",
        dest="ssh_key",
        action="append",
        help="SSH key name to add (can be specified multiple times)",
    )
    create_parser.add_argument(
        "--count",
        "-c",
        type=int,
        default=1,
        help="Number of instances to launch (default: 1)",
    )
    create_parser.add_argument(
        "--name",
        "-n",
        help="Name for the instance",
    )
    create_parser.add_argument(
        "--output",
        "-o",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text)",
    )
    create_parser.add_argument(
        "--type-filter",
        "-f",
        help="Comma-separated list of instance types to filter (e.g., 'a100,h100')",
    )
    create_parser.add_argument(
        "--wait",
        "-w",
        action="store_true",
        help="Wait until the instance is running and responding to ping before completing",
    )

    # Delete instance command
    delete_parser = subparsers.add_parser(
        "delete", help="Delete a Lambda instance"
    )
    delete_parser.add_argument(
        "instance_id",
        help="ID of the instance to delete",
    )

    # List instances command
    list_parser = subparsers.add_parser(
        "list", help="List all running instances"
    )
    list_parser.add_argument(
        "--output",
        "-o",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text)",
    )

    # Get machine types info command
    instances_parser = subparsers.add_parser(
        "instances", help="Show available instance types"
    )
    instances_parser.add_argument(
        "--output",
        "-o",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text)",
    )



    # Restart instance command
    restart_parser = subparsers.add_parser(
        "restart", help="Restart a Lambda instance"
    )
    restart_parser.add_argument(
        "instance_id",
        help="ID of the instance to restart",
    )
    restart_parser.add_argument(
        "--output",
        "-o",
        choices=["text", "json"],
        default="text",
        help="Output format (default: text)",
    )

    parser.add_argument(
        "--api-key",
        help="Lambda Labs API key (or set LAMBDA_API_KEY environment variable)",
    )

    args = parser.parse_args()

    # Handle environment variable fallback for api-key
    if args.api_key is None:
        args.api_key = os.environ.get("LAMBDA_API_KEY")

    # Initialize manager
    try:
        manager = LambdaInstanceManager(api_key=args.api_key, default_ssh_key=os.environ.get("LAMBDA_DEFAULT_SSH_KEY"))
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    # Execute command
    try:
        if args.command == "create":
            # Parse type filter if provided
            instance_type_filter = None
            if hasattr(args, "type_filter") and args.type_filter:
                instance_type_filter = {t.strip() for t in args.type_filter.split(",") if t.strip()}

            result = manager.launch_instance(
                region=args.region,
                ssh_key_names=args.ssh_key,
                quantity=args.count,
                name=args.name,
                instance_type_filter=instance_type_filter,
            )

            # Handle both wrapped and unwrapped response formats
            instances_list = None
            if isinstance(result, dict):
                if "instances" in result and isinstance(result["instances"], list):
                    instances_list = result["instances"]
                elif "data" in result and isinstance(result["data"], list):
                    instances_list = result["data"]

            if args.output == "json":
                print(json.dumps(result, indent=2))
            else:
                print("Instance launched!")
                if instances_list and len(instances_list) > 0:
                    instance = instances_list[0]
                    if isinstance(instance, dict):
                        print(f"Instance ID: {instance.get('id', 'N/A')}")
                        print(f"Name: {instance.get('name', 'N/A')}")
                        print(f"Status: {instance.get('status', 'N/A')}")
                        if "ip" in instance:
                            print(f"IP Address: {instance['ip']}")
                if isinstance(result, dict) and "error" in result:
                    print(f"Warning: {result['error']}")

            # Always show connection info after launching
            if instances_list and len(instances_list) > 0:
                for inst in instances_list:
                    if isinstance(inst, dict):
                        ip_val = inst.get("ip", inst.get("private_ip", "N/A"))
                        if ip_val and ip_val != "N/A":
                            print(f"Connect: ssh -o StrictHostKeyChecking=no ec2-user@{ip_val}")
                            jupyter_url = inst.get("jupyter_url", "")
                            if jupyter_url:
                                print(f"Jupyter: {jupyter_url}")

            if hasattr(args, "wait") and args.wait:
                # Get the instance ID and poll until it's reachable via ping
                if instances_list and len(instances_list) > 0:
                    instance = instances_list[0]
                    if isinstance(instance, dict):
                        ip_val = instance.get("ip", instance.get("private_ip", "N/A"))
                        if ip_val and ip_val != "N/A":
                            print(f"Waiting for instance at {ip_val} to be reachable...")
                            pingable = False
                            attempt = 0
                            max_attempts = 120  # 20 minutes (120 * 10 seconds)

                            while not pingable and attempt < max_attempts:
                                attempt += 1
                                pingable = os.system(f"ping -c 1 -W 1 {ip_val} >/dev/null 2>&1") == 0
                                if not pingable:
                                    print(f"  Attempt {attempt}/{max_attempts}: Not yet reachable...")
                                    import time
                                    time.sleep(10)

                            if pingable:
                                print(f"Instance {ip_val} is now reachable!")
                            else:
                                print(f"Instance {ip_val} is not reachable after {max_attempts} attempts. Exiting.")
                                sys.exit(1)

        elif args.command == "delete":
            success = manager.delete_instance(args.instance_id)
            if not success:
                sys.exit(1)

        elif args.command == "list":
            instances = manager.get_instances()

            if args.output == "json":
                print(json.dumps(instances, indent=2))
            else:
                if instances:
                    print(f"Found {len(instances)} instance(s):")
                    # Collect all prices to calculate total
                    total_price = 0
                    total_runtime_hours = 0
                    total_running_cost = 0
                    for instance_id, instance in instances.items():
                        print(f"\n- {instance_id}")
                        print(f"  Name: {instance.get('name', 'N/A')}")
                        print(f"  Status: {instance.get('status', 'N/A')}")
                        # Try both instance_type_name and instance_type.name
                        type_name = instance.get('instance_type_name')
                        if not type_name and isinstance(instance.get('instance_type'), dict):
                            type_name = instance.get('instance_type', {}).get('name', 'N/A')
                        print(f"  Type: {type_name}")

                        # Get price from instance_type object without extra API call
                        price_dollars = None
                        if isinstance(instance.get('instance_type'), dict):
                            price_cents = instance.get('instance_type', {}).get('price_cents_per_hour')
                            if price_cents:
                                price_dollars = price_cents / 100.0
                        if price_dollars is not None:
                            print(f"  Price: ${price_dollars:.2f}/hr")
                            total_price += price_dollars

                        # Show specs if available
                        specs = instance.get('instance_type', {}).get('specs', {})
                        if specs and isinstance(specs, dict):
                            cpu = specs.get('vcpus', 'N/A')
                            memory = specs.get('memory_gib', 'N/A')
                            storage = specs.get('storage_gib', 'N/A')
                            gpus = specs.get('gpus', 'N/A')
                            print(f"  CPU: {cpu}, Memory: {memory} GiB, Storage: {storage} GiB, GPUs: {gpus}")

                        if "ip" in instance:
                            print(f"  IP: {instance['ip']}")

                            # Try to get uptime via SSH and calculate runtime/cost
                            try:
                                import subprocess
                                result = subprocess.run(
                                    ['ssh', '-o', 'StrictHostKeyChecking=no', '-o', 'ConnectTimeout=5', '-o', 'BatchMode=yes', 'ubuntu@' + instance['ip'], 'uptime -p'],
                                    capture_output=True,
                                    text=True,
                                    timeout=15
                                )
                                if result.returncode == 0:
                                    uptime = result.stdout.strip()
                                    runtime_hours = parse_uptime_to_hours(uptime)
                                    print(f"  Uptime: {uptime}")
                                    print(f"  Runtime: {runtime_hours:.2f} hours")

                                    # Calculate and display individual instance cost
                                    if price_dollars is not None:
                                        instance_cost = price_dollars * runtime_hours
                                        print(f"  Cost: ${instance_cost:.2f}")

                                        # Add to total runtime and cost
                                        total_runtime_hours += runtime_hours
                                        total_running_cost += instance_cost
                            except FileNotFoundError:
                                pass  # SSH not installed
                            except Exception:
                                # SSH check failed, skip uptime calculation
                                pass

                    # Show total
                    print(f"\n=== TOTAL: ${total_price:.2f}/hr for {len(instances)} instance(s) ===")
                    if total_runtime_hours > 0:
                        print(f"=== TOTAL RUNNING COST: ${total_running_cost:.2f} across {total_runtime_hours:.2f} hours ===")
                else:
                    print("No instances found.")

        elif args.command == "instances":
            response = manager._api_request("GET", "/instance-types")
            types = response.get("data", response)

            if not types:
                print("No instance types available")
                return

            # Parse instance types
            available_types = []
            unavailable_types = []

            if isinstance(types, list):
                for t in types:
                    if isinstance(t, dict):
                        inst_type = t.get("instance_type", t)
                        if isinstance(inst_type, dict):
                            regions = t.get("regions_with_capacity_available", [])
                            price_cents = inst_type.get("price_cents_per_hour", 0)
                            price_dollars = price_cents / 100.0 if price_cents else 0
                            parsed = {
                                "name": inst_type.get("name", "unknown"),
                                "description": inst_type.get("description", "unknown"),
                                "gpus": inst_type.get("specs", {}).get("gpus", 1),
                                "price_per_hour": price_dollars
                            }
                            if len(regions) > 0:
                                available_types.append(parsed)
                            else:
                                unavailable_types.append(parsed)
            elif isinstance(types, dict):
                for name, info in types.items():
                    if isinstance(info, dict):
                        inst_type = info.get("instance_type", info)
                        if isinstance(inst_type, dict):
                            regions = info.get("regions_with_capacity_available", [])
                            # Get price - it's in cents, convert to dollars
                            price_cents = inst_type.get("price_cents_per_hour", 0)
                            price_dollars = price_cents / 100.0 if price_cents else 0
                            parsed = {
                                "name": name,
                                "description": inst_type.get("description", "unknown"),
                                "gpus": inst_type.get("specs", {}).get("gpus", 1),
                                "price_per_hour": price_dollars
                            }
                            if len(regions) > 0:
                                available_types.append(parsed)
                            else:
                                unavailable_types.append(parsed)

            # Sort by GPU count then by name
            available_types.sort(key=lambda x: (x["gpus"], x["name"]))
            unavailable_types.sort(key=lambda x: (x["gpus"], x["name"]))

            if args.output == "json":
                print(json.dumps({"available": available_types, "unavailable": unavailable_types}, indent=2))
            else:
                print("=== AVAILABLE INSTANCE TYPES ===")
                if available_types:
                    for t in available_types:
                        price_str = f" @ ${t['price_per_hour']:.2f}/hr" if t.get('price_per_hour') else ""
                        print(f"- {t['name']}: {t['description']} ({t['gpus']} GPU{'s' if t['gpus'] > 1 else ''}){price_str}")
                else:
                    print("(none)")

                print("\n=== UNAVAILABLE INSTANCE TYPES ===")
                if unavailable_types:
                    for t in unavailable_types:
                        price_str = f" @ ${t['price_per_hour']:.2f}/hr" if t.get('price_per_hour') else ""
                        print(f"- {t['name']}: {t['description']} ({t['gpus']} GPU{'s' if t['gpus'] > 1 else ''}){price_str}")
                else:
                    print("(none)")

        elif args.command == "restart":
            result = manager.restart_instance(args.instance_id)
            if args.output == "json":
                print(json.dumps(result, indent=2))

        else:
            parser.print_help()

    except LambdaAPIError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Unexpected error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
