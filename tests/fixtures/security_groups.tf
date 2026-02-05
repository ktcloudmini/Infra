# ALB 보안 그룹
resource "aws_security_group" "alb_sg" {
  name        = "test-z33-web-alb-sg"
  description = "ALB Security Group"
  vpc_id      = module.vpc.vpc_id

  tags = { Name = "test-z33-web-alb-sg" }
}

# App 보안 그룹 (EC2 직접 접근할 수 없고, ALB로부터만 8080 허용)
resource "aws_security_group" "app_sg" {
  name        = "test-z33-web-app-sg"
  description = "App Security Group"
  vpc_id      = module.vpc.vpc_id

  tags = { Name = "test-z33-web-app-sg" }
}

# --- ALB 보안그룹 규칙 정의 ---

# ALB Ingress: 외부에서 80 포트 접속 허용
resource "aws_security_group_rule" "alb_ingress_80" {
  type              = "ingress"
  from_port         = 80
  to_port           = 80
  protocol          = "tcp"
  cidr_blocks       = ["0.0.0.0/0"]
  security_group_id = aws_security_group.alb_sg.id
}

# ALB Egress: App SG를 가진 인스턴스의 8080으로만 전달 허용
resource "aws_security_group_rule" "alb_egress_to_app" {
  type                     = "egress"
  from_port                = 8080
  to_port                  = 8080
  protocol                 = "tcp"
  source_security_group_id = aws_security_group.app_sg.id
  security_group_id        = aws_security_group.alb_sg.id
}

# --- App 보안그룹 규칙 정의 ---

# App Ingress: ALB SG로부터 들어오는 8080만 허용 (EC2 직접 접속 차단)
resource "aws_security_group_rule" "app_ingress_from_alb" {
  type                     = "ingress"
  from_port                = 8080
  to_port                  = 8080
  protocol                 = "tcp"
  source_security_group_id = aws_security_group.alb_sg.id
  security_group_id        = aws_security_group.app_sg.id
}

# App Egress: 인터넷으로 나가는 트래픽 허용 (apt update 등)
resource "aws_security_group_rule" "app_egress_all" {
  type              = "egress"
  from_port         = 0
  to_port           = 0
  protocol          = "-1"
  cidr_blocks       = ["0.0.0.0/0"]
  security_group_id = aws_security_group.app_sg.id
}