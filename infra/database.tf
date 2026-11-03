# Temporal's persistence and visibility stores. The master password is
# generated and kept by RDS in Secrets Manager; it never goes through
# Terraform state or a tfvars file.

resource "aws_db_subnet_group" "temporal" {
  name       = "${var.project}-temporal"
  subnet_ids = aws_subnet.private[*].id
}

resource "aws_db_instance" "temporal" {
  identifier                  = "${var.project}-temporal"
  engine                      = "postgres"
  engine_version              = "16"
  instance_class              = "db.t4g.micro"
  allocated_storage           = 20
  storage_encrypted           = true
  username                    = "temporal"
  manage_master_user_password = true
  db_subnet_group_name        = aws_db_subnet_group.temporal.name
  vpc_security_group_ids      = [aws_security_group.database.id]
  publicly_accessible         = false
  backup_retention_period     = 1
  skip_final_snapshot         = true # demo: `terraform destroy` leaves nothing behind
  deletion_protection         = false
}
