import pytest
import boto3
import json
import os
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "infra_config.json")

def get_option(request, key, cli_opt):
    val = request.config.getoption(cli_opt)
    if val: return val
    val = os.getenv(key)
    if val: return val
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, 'r') as f:
                data = json.load(f)
                mapping = {
                    "ALB_URL": "alb_dns_name",
                    "ASG_NAME": "asg_name",
                    "TG_ARN": "target_group_arn"
                }
                json_key = mapping.get(key)
                if json_key in data:
                    return data[json_key]["value"]
        except: pass
    return None

def pytest_addoption(parser):
    parser.addoption("--alb-url", action="store")
    parser.addoption("--asg-name", action="store")
    parser.addoption("--tg-arn", action="store")

@pytest.fixture(scope="session")
def alb_url(request):
    url = get_option(request, "ALB_URL", "--alb-url")
    if url and not url.startswith("http"):
        url = f"http://{url}"
    return url.rstrip('/') if url else None

@pytest.fixture(scope="session")
def asg_name(request):
    return get_option(request, "ASG_NAME", "--asg-name")

@pytest.fixture(scope="session")
def tg_arn(request):
    return get_option(request, "TG_ARN", "--tg-arn")

@pytest.fixture(scope="session")
def asg_client():
    return boto3.client("autoscaling", region_name="ap-northeast-2")

@pytest.fixture(scope="session")
def elbv2_client():
    return boto3.client("elbv2", region_name="ap-northeast-2")

@pytest.fixture(scope="session")
def ec2_client():
    return boto3.client("ec2", region_name="ap-northeast-2")