import * as cdk from 'aws-cdk-lib';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import { Construct } from 'constructs';

export interface ComputeStackProps extends cdk.StackProps {
  projectName: string;
  vpc: ec2.Vpc;
  documentBucket: s3.Bucket;
  /** Neo4j connection credentials, created by the data stack. */
  neo4jSecret: secretsmanager.ISecret;
  /** Bedrock inference-profile id. Defaults to the library's current Claude id. */
  bedrockModelId?: string;
  /** Bedrock embedding model id. */
  bedrockEmbeddingModelId?: string;
}

/** Cross-region inference-profile id — current Claude models on Bedrock are not
 *  invokable by their bare foundation-model id. Keep in step with
 *  `neo4j_agent_memory.integrations.strands.bedrock_llm_model()`. */
const DEFAULT_BEDROCK_MODEL_ID = 'us.anthropic.claude-sonnet-4-6';
const DEFAULT_BEDROCK_EMBEDDING_MODEL_ID = 'amazon.titan-embed-text-v2:0';

export class ComputeStack extends cdk.Stack {
  public readonly apiHandler: lambda.Function;

  constructor(scope: Construct, id: string, props: ComputeStackProps) {
    super(scope, id, props);

    // Lambda execution role
    const lambdaRole = new iam.Role(this, 'LambdaRole', {
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSLambdaVPCAccessExecutionRole'),
      ],
    });

    // Bedrock permissions
    lambdaRole.addToPolicy(
      new iam.PolicyStatement({
        actions: [
          'bedrock:InvokeModel',
          'bedrock:InvokeModelWithResponseStream',
        ],
        resources: ['*'], // Scope to specific models in production
      })
    );

    // Read the Neo4j credentials secret (resource-scoped, not a wildcard).
    props.neo4jSecret.grantRead(lambdaRole);

    // S3 permissions
    props.documentBucket.grantReadWrite(lambdaRole);

    // Lambda security group
    const lambdaSg = new ec2.SecurityGroup(this, 'LambdaSg', {
      vpc: props.vpc,
      description: 'Security group for Financial Services Advisor Lambda',
      allowAllOutbound: true,
    });

    // Main API Lambda function
    this.apiHandler = new lambda.Function(this, 'ApiHandler', {
      functionName: `${props.projectName}-api`,
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'handler.handler',
      // `pip install .` resolves the backend's declared dependencies from PyPI
      // and installs its own `src` package, so there is no generated
      // requirements.txt to keep in step with pyproject.toml. Local development
      // resolves neo4j-agent-memory from the repo via [tool.uv.sources]; pip
      // ignores that table and installs the published pin instead, which is
      // what a deployment wants.
      code: lambda.Code.fromAsset('../backend', {
        exclude: ['.venv', '.ruff_cache', '.pytest_cache', '__pycache__', 'tests', '*.pyc'],
        bundling: {
          image: lambda.Runtime.PYTHON_3_12.bundlingImage,
          command: [
            'bash',
            '-c',
            'pip install . --target /asset-output && cp handler.py /asset-output/',
          ],
        },
      }),
      memorySize: 1024,
      timeout: cdk.Duration.seconds(120),
      role: lambdaRole,
      vpc: props.vpc,
      vpcSubnets: {
        subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS,
      },
      securityGroups: [lambdaSg],
      environment: {
        LOG_LEVEL: 'INFO',
        S3_BUCKET_NAME: props.documentBucket.bucketName,
        // AWS_REGION is reserved: Lambda injects it, and CloudFormation rejects
        // a function that sets it.
        NEO4J_SECRET_ARN: props.neo4jSecret.secretArn,
        BEDROCK_MODEL_ID: props.bedrockModelId ?? DEFAULT_BEDROCK_MODEL_ID,
        BEDROCK_EMBEDDING_MODEL_ID:
          props.bedrockEmbeddingModelId ?? DEFAULT_BEDROCK_EMBEDDING_MODEL_ID,
      },
      // `logRetention` is deprecated (it provisions a custom resource); an
      // explicit LogGroup is the current idiom.
      logGroup: new logs.LogGroup(this, 'ApiHandlerLogs', {
        logGroupName: `/aws/lambda/${props.projectName}-api`,
        retention: logs.RetentionDays.ONE_MONTH,
        removalPolicy: cdk.RemovalPolicy.DESTROY,
      }),
    });

    // Outputs
    new cdk.CfnOutput(this, 'LambdaFunctionArn', {
      value: this.apiHandler.functionArn,
      description: 'API Lambda function ARN',
    });
  }
}
