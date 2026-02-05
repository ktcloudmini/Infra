# ---- Network (VPC) : NAT 제거, Public Subnet만 사용
module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "5.0.0"

  name = "test-z33-web-vpc"
  cidr = "10.0.0.0/16"

  azs            = ["ap-northeast-2a", "ap-northeast-2c"]
  public_subnets = ["10.0.1.0/24", "10.0.2.0/24"]

  enable_nat_gateway = false

  enable_dns_hostnames = true
  enable_dns_support   = true
}