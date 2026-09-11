#!/usr/bin/env node
import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import { NetworkStack } from '../lib/stacks/network-stack';
import { AuthStack } from '../lib/stacks/auth-stack';
import { DataStack } from '../lib/stacks/data-stack';
import { ComputeStack } from '../lib/stacks/compute-stack';
import { ApiStack } from '../lib/stacks/api-stack';
import { MonitoringStack } from '../lib/stacks/monitoring-stack';

const app = new cdk.App();

const env = {
  account: process.env.CDK_DEFAULT_ACCOUNT,
  region: process.env.CDK_DEFAULT_REGION || 'us-east-1',
};

const projectName = 'financial-services-advisor';

// Network Stack - VPC and networking
const networkStack = new NetworkStack(app, `${projectName}-network`, {
  env,
  projectName,
});

// Auth Stack - Cognito
const authStack = new AuthStack(app, `${projectName}-auth`, {
  env,
  projectName,
});

// Data Stack - S3, Neo4j configuration
const dataStack = new DataStack(app, `${projectName}-data`, {
  env,
  projectName,
  vpc: networkStack.vpc,
});

// Compute Stack - Lambda, Step Functions
const computeStack = new ComputeStack(app, `${projectName}-compute`, {
  env,
  projectName,
  vpc: networkStack.vpc,
  documentBucket: dataStack.documentBucket,
  // Without the secret the function has no Neo4j credentials at all.
  neo4jSecret: dataStack.neo4jSecret,
});
computeStack.addStackDependency(dataStack);

// API Stack - API Gateway, CloudFront
const apiStack = new ApiStack(app, `${projectName}-api`, {
  env,
  projectName,
  lambdaFunction: computeStack.apiHandler,
  userPool: authStack.userPool,
  // Set to false when `../frontend/dist` has not been built yet; `cdk synth`
  // fails on a missing asset directory otherwise.
  deployFrontend: app.node.tryGetContext('deployFrontend') !== 'false',
});

// Monitoring Stack - CloudWatch dashboards and alarms
new MonitoringStack(app, `${projectName}-monitoring`, {
  env,
  projectName,
  apiGateway: apiStack.api,
  lambdaFunction: computeStack.apiHandler,
});

app.synth();
