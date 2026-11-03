# Two AZs: public subnets hold only the NAT gateway; everything that runs
# (Temporal, the database, the worker, the sandbox, the receiver) is private.

data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  azs = slice(data.aws_availability_zones.available.names, 0, 2)
}

resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = { Name = var.project }
}

resource "aws_subnet" "public" {
  count             = 2
  vpc_id            = aws_vpc.main.id
  availability_zone = local.azs[count.index]
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, count.index)
  tags              = { Name = "${var.project}-public-${count.index}" }
}

resource "aws_subnet" "private" {
  count             = 2
  vpc_id            = aws_vpc.main.id
  availability_zone = local.azs[count.index]
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, 10 + count.index)
  tags              = { Name = "${var.project}-private-${count.index}" }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
}

resource "aws_eip" "nat" {
  domain = "vpc"
}

# One NAT gateway, not one per AZ: this is a demo-sized deployment.
resource "aws_nat_gateway" "main" {
  allocation_id = aws_eip.nat.id
  subnet_id     = aws_subnet.public[0].id
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }
}

resource "aws_route_table" "private" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.main.id
  }
}

resource "aws_route_table_association" "public" {
  count          = 2
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table_association" "private" {
  count          = 2
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private.id
}

# --- Security groups: each one only what its service needs -----------------

resource "aws_security_group" "temporal" {
  name        = "${var.project}-temporal"
  description = "Temporal frontend: gRPC from the worker and the receiver only"
  vpc_id      = aws_vpc.main.id
}

resource "aws_security_group" "database" {
  name        = "${var.project}-database"
  description = "Postgres: from Temporal only"
  vpc_id      = aws_vpc.main.id
}

resource "aws_security_group" "worker" {
  name        = "${var.project}-worker"
  description = "Worker: no inbound; outbound to Temporal and the internet via NAT"
  vpc_id      = aws_vpc.main.id
}

resource "aws_security_group" "receiver" {
  name        = "${var.project}-receiver"
  description = "Receiver Lambda: no inbound; outbound to Temporal"
  vpc_id      = aws_vpc.main.id
}

resource "aws_security_group" "sandbox" {
  name        = "${var.project}-sandbox"
  description = "Sandbox: no inbound; HTTPS out only (package index, model API, S3)"
  vpc_id      = aws_vpc.main.id
}

resource "aws_vpc_security_group_ingress_rule" "temporal_from_worker" {
  security_group_id            = aws_security_group.temporal.id
  referenced_security_group_id = aws_security_group.worker.id
  ip_protocol                  = "tcp"
  from_port                    = 7233
  to_port                      = 7233
}

resource "aws_vpc_security_group_ingress_rule" "temporal_from_receiver" {
  security_group_id            = aws_security_group.temporal.id
  referenced_security_group_id = aws_security_group.receiver.id
  ip_protocol                  = "tcp"
  from_port                    = 7233
  to_port                      = 7233
}

resource "aws_vpc_security_group_ingress_rule" "database_from_temporal" {
  security_group_id            = aws_security_group.database.id
  referenced_security_group_id = aws_security_group.temporal.id
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
}

resource "aws_vpc_security_group_egress_rule" "temporal_out" {
  security_group_id = aws_security_group.temporal.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

resource "aws_vpc_security_group_egress_rule" "worker_out" {
  security_group_id = aws_security_group.worker.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

resource "aws_vpc_security_group_egress_rule" "receiver_to_temporal" {
  security_group_id            = aws_security_group.receiver.id
  referenced_security_group_id = aws_security_group.temporal.id
  ip_protocol                  = "tcp"
  from_port                    = 7233
  to_port                      = 7233
}

# HTTPS only. An allow-list proxy would narrow this to the package index and
# the model API (see ADR 0005); a security group can't filter by hostname.
resource "aws_vpc_security_group_egress_rule" "sandbox_https" {
  security_group_id = aws_security_group.sandbox.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}
