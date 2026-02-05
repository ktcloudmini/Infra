#---- Outputs
output "alb_dns_name" {
  description = "url"
  value       = "http://${aws_lb.main.dns_name}"
}

output "target_group_arn" {
  description = "Target Group ARN"
  value       = aws_lb_target_group.app_tg.arn
}

output "public_subnet_ids" {
  description = "Public Subnets where Apps are running"
  value       = module.vpc.public_subnets
}

output "vpc_id" {
  value = module.vpc.vpc_id
}

output "asg_name" {
  value = aws_autoscaling_group.app_asg.name
}