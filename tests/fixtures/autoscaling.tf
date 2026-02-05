# ---- Auto Scaling Group
resource "aws_autoscaling_group" "app_asg" {
  name                = "test-z33-web-asg"
  vpc_zone_identifier = module.vpc.public_subnets
  target_group_arns   = [aws_lb_target_group.app_tg.arn]

  health_check_type         = "ELB"
  health_check_grace_period = 120

  min_size         = 2
  max_size         = 4
  desired_capacity = 2


  launch_template {
    id      = aws_launch_template.app_lt.id
    version = "$Latest"
  }

  force_delete = true

  instance_refresh {
    strategy = "Rolling"
    preferences {
      min_healthy_percentage = 50
    }
  }
}

#---- Scaling Policy
resource "aws_autoscaling_policy" "cpu_target" {
  name                   = "test-z33-web-cpu-scaling-policy"
  autoscaling_group_name = aws_autoscaling_group.app_asg.name
  policy_type            = "TargetTrackingScaling"
  estimated_instance_warmup = 180

  target_tracking_configuration {
    predefined_metric_specification {
      predefined_metric_type = "ASGAverageCPUUtilization"
    }
    target_value = 70.0 #scaling test 용으로 올려두었습니다. 원래 50-60정도.
  }
}