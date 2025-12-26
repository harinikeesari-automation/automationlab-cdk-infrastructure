from aws_cdk import (
    Stack,
    aws_ec2 as ec2,
    aws_rds as rds,
    Tags,
    RemovalPolicy,
    CfnOutput,
    aws_iam as iam,
    aws_scheduler as scheduler
)
from constructs import Construct


class CdkProjectStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Create a VPC with default settings (public and private subnets)
        vpc = ec2.Vpc(self, "Vpc", max_azs=2)

        # Security group allowing SSH (port 22) from anywhere — adjust for production
        sg = ec2.SecurityGroup(
            self,
            "InstanceSecurityGroup",
            vpc=vpc,
            description="Allow SSH access",
            allow_all_outbound=True,
        )
        sg.add_ingress_rule(ec2.Peer.any_ipv4(), ec2.Port.tcp(22), "allow SSH")

        # EC2 instance using Amazon Linux 2
        instance = ec2.Instance(
            self,
            "Ec2Instance",
            instance_type=ec2.InstanceType("t3.micro"),
            machine_image=ec2.MachineImage.latest_amazon_linux2(),
            vpc=vpc,
            security_group=sg,
        )
          # Security group for RDS allowing access from the EC2 instance's security group
        rds_sg = ec2.SecurityGroup(
            self,
            "RdsSecurityGroup",
            vpc=vpc,
            description="Allow database access",
            allow_all_outbound=True,
        )
        # rds_sg.add_ingress_rule(
        #     ec2.Peer.ipv4(vpc.vpc_cidr_block),
        #     ec2.Port.tcp(5432),
        #     "Allow connection from within VPC"
        # )

        # RDS instance (e.g., MySQL)
     
        rds_instance = rds.DatabaseInstance(
            self,
            "RdsInstance",
            engine=rds.DatabaseInstanceEngine.mysql(
                version=rds.MysqlEngineVersion.VER_8_0_43
            ),
           instance_type=ec2.InstanceType.of(
                ec2.InstanceClass.T3, 
                ec2.InstanceSize.MICRO
            ),
            vpc=vpc,
            security_groups=[rds_sg],
            multi_az=False,
            allocated_storage=20,
            storage_type=rds.StorageType.GP2,
            deletion_protection=False,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # --- 4. Scheduling Logic (Start/Stop) ---
        
        # A. Create an IAM Role that allows the Scheduler to Start/Stop this specific RDS
        scheduler_role = iam.Role(
            self, "SchedulerRole",
            assumed_by=iam.ServicePrincipal("scheduler.amazonaws.com")
        )

        # Attach permissions to the role
        scheduler_role.add_to_policy(iam.PolicyStatement(
            actions=["rds:StartDBInstance", "rds:StopDBInstance"],
            resources=[rds_instance.instance_arn]
        ))

        # B. Define the Target Object (The API call payload)
        # We need to pass the DBInstanceIdentifier to the API call
        target_payload = {
            "DbInstanceIdentifier": rds_instance.instance_identifier
        }
        # C. Schedule: Stop at 6:00 PM (18:00) every day
        stop_schedule = scheduler.CfnSchedule(
            self, "StopRdsSchedule",
            flexible_time_window=scheduler.CfnSchedule.FlexibleTimeWindowProperty(mode="OFF"),
            schedule_expression="cron(30 6 * * ? *)", # UTC time. Adjust for your timezone!
            target=scheduler.CfnSchedule.TargetProperty(
                arn= "arn:aws:scheduler:::aws-sdk:rds:stopDBInstance",
                role_arn=scheduler_role.role_arn,
                input=f'{{"DbInstanceIdentifier": "{rds_instance.instance_identifier}"}}'
            ),
            description="Stops RDS instance at 6 PM UTC"
        )

        # D. Schedule: Start at 8:00 AM every Mon-Fri
        start_schedule = scheduler.CfnSchedule(
            self, "StartRdsSchedule",
            flexible_time_window=scheduler.CfnSchedule.FlexibleTimeWindowProperty(mode="OFF"),
            schedule_expression="cron(30 22 ? * MON-FRI *)", # UTC time.
            target=scheduler.CfnSchedule.TargetProperty(
                arn="arn:aws:scheduler:::aws-sdk:rds:startDBInstance",
                role_arn=scheduler_role.role_arn,
                input=f'{{"DbInstanceIdentifier": "{rds_instance.instance_identifier}"}}'
            ),
            description="Starts RDS instance at 8 AM UTC (Mon-Fri)"
        )

        # Optionally add user data or tag the instance
        Tags.of(instance).add("Name", "cdk-ec2-instance")
        Tags.of(rds_instance).add("Name", "cdk-rds-instance")
        # Allow the EC2 instance to connect to the RDS instance on the default port
        rds_sg.add_ingress_rule(sg, ec2.Port.tcp(3306), "Allow MySQL access from EC2 instance")

        CfnOutput(self, "DBEndpoint", value=rds_instance.db_instance_endpoint_address)
        CfnOutput(self, "EC2InstancePrivateIP", value=instance.instance_private_ip)