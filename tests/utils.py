import re
from datetime import datetime
import requests

# ---- Constants
DEFAULT_REQUEST_TIMEOUT = 2

# --- Helpers
def now_str():
    """현재 시간을 HH:MM:SS 문자열로 반환"""
    return datetime.now().strftime("%H:%M:%S")

def extract_response_host(response_text):
    """
    응답 본문에서 Host / Hostname 값 추출
    """
    match = re.search(r"(?:Host|Hostname):\s*(.*?)(?:<|\s|$)", response_text)
    return match.group(1).strip() if match else None

def is_service_available(url, timeout=DEFAULT_REQUEST_TIMEOUT):
    """ALB 주소로 요청시 정상 응답(HTTP 200)하는지 확인"""
    try:
        return requests.get(url, timeout=timeout).status_code == 200
    except Exception:
        return False

# ---- Helpers on AWS
def get_healthy_instance_ids(elbv2_client, tg_arn):
    """Target Group 내 Healthy 상태인 인스턴스 ID 목록 반환"""
    resp = elbv2_client.describe_target_health(TargetGroupArn=tg_arn)
    return [
        t["Target"]["Id"]
        for t in resp["TargetHealthDescriptions"]
        if t["TargetHealth"]["State"] == "healthy"
    ]


def get_asg_desired_capacity(asg_client, asg_name):
    """ASG의 Desired Capacity 조회"""
    resp = asg_client.describe_auto_scaling_groups(
        AutoScalingGroupNames=[asg_name]
    )
    if not resp["AutoScalingGroups"]:
        return 0
    return resp["AutoScalingGroups"][0]["DesiredCapacity"]

def get_healthy_target_instance_ids(elbv2_client, target_group_arn):
    response = elbv2_client.describe_target_health(
        TargetGroupArn=target_group_arn
    )
    return [
        t["Target"]["Id"]
        for t in response["TargetHealthDescriptions"]
        if t["TargetHealth"]["State"] == "healthy"
    ]