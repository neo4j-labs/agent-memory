import * as cdk from 'aws-cdk-lib';
import * as apigateway from 'aws-cdk-lib/aws-apigateway';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as origins from 'aws-cdk-lib/aws-cloudfront-origins';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as s3deploy from 'aws-cdk-lib/aws-s3-deployment';
import { Construct } from 'constructs';

export interface ApiStackProps extends cdk.StackProps {
  projectName: string;
  lambdaFunction: lambda.Function;
  userPool: cognito.UserPool;
  /** Upload `../frontend/dist` to the frontend bucket. Requires a built
   *  frontend; pass `-c deployFrontend=false` to synth without one. */
  deployFrontend?: boolean;
}

export class ApiStack extends cdk.Stack {
  public readonly api: apigateway.RestApi;
  public readonly distribution: cloudfront.Distribution;

  constructor(scope: Construct, id: string, props: ApiStackProps) {
    super(scope, id, props);

    // API Gateway
    this.api = new apigateway.RestApi(this, 'Api', {
      restApiName: `${props.projectName}-api`,
      description: 'Financial Services Advisor API',
      deployOptions: {
        stageName: 'v1',
        throttlingBurstLimit: 100,
        throttlingRateLimit: 50,
      },
      defaultCorsPreflightOptions: {
        allowOrigins: apigateway.Cors.ALL_ORIGINS,
        allowMethods: apigateway.Cors.ALL_METHODS,
        allowHeaders: ['Content-Type', 'Authorization', 'X-Amz-Date', 'X-Api-Key'],
      },
    });

    // Cognito Authorizer
    const authorizer = new apigateway.CognitoUserPoolsAuthorizer(this, 'Authorizer', {
      cognitoUserPools: [props.userPool],
      authorizerName: `${props.projectName}-authorizer`,
    });

    // Lambda integration. No requestTemplates: the proxy integration must pass
    // the request through unchanged for FastAPI (via Mangum) to route it.
    const lambdaIntegration = new apigateway.LambdaIntegration(props.lambdaFunction);

    // One authorized proxy for the whole API rather than a hand-maintained list
    // of methods. FastAPI serves ~38 routes; enumerating seven of them meant
    // /api/chat/stream, /api/graph/*, /api/traces/* and /api/reports/* were
    // simply unreachable, and the enumeration drifted every time a route was
    // added.
    const apiResource = this.api.root.addResource('api');
    apiResource.addProxy({
      anyMethod: true,
      defaultIntegration: lambdaIntegration,
      defaultMethodOptions: {
        authorizer,
        authorizationType: apigateway.AuthorizationType.COGNITO,
      },
    });

    // Health endpoint (no auth required, so orchestrators can probe it)
    const healthResource = this.api.root.addResource('health');
    healthResource.addMethod('GET', lambdaIntegration);

    // S3 bucket for frontend hosting
    const frontendBucket = new s3.Bucket(this, 'FrontendBucket', {
      bucketName: `${props.projectName}-frontend-${this.account}`,
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      autoDeleteObjects: true,
    });

    // CloudFront distribution
    this.distribution = new cloudfront.Distribution(this, 'Distribution', {
      defaultBehavior: {
        origin: origins.S3BucketOrigin.withOriginAccessControl(frontendBucket),
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        cachePolicy: cloudfront.CachePolicy.CACHING_OPTIMIZED,
      },
      additionalBehaviors: {
        '/api/*': {
          origin: new origins.RestApiOrigin(this.api),
          viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.HTTPS_ONLY,
          cachePolicy: cloudfront.CachePolicy.CACHING_DISABLED,
          originRequestPolicy: cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
          allowedMethods: cloudfront.AllowedMethods.ALLOW_ALL,
        },
      },
      defaultRootObject: 'index.html',
      errorResponses: [
        {
          httpStatus: 404,
          responseHttpStatus: 200,
          responsePagePath: '/index.html',
        },
      ],
    });

    // Upload the built frontend. Without this the bucket is created, fronted by
    // CloudFront, and empty.
    if (props.deployFrontend ?? true) {
      new s3deploy.BucketDeployment(this, 'FrontendDeploy', {
        sources: [s3deploy.Source.asset('../frontend/dist')],
        destinationBucket: frontendBucket,
        distribution: this.distribution,
        distributionPaths: ['/*'],
      });
    }

    // Outputs
    new cdk.CfnOutput(this, 'ApiUrl', {
      value: this.api.url,
      description: 'API Gateway URL',
    });

    new cdk.CfnOutput(this, 'CloudFrontUrl', {
      value: `https://${this.distribution.distributionDomainName}`,
      description: 'CloudFront distribution URL',
    });

    new cdk.CfnOutput(this, 'FrontendBucketName', {
      value: frontendBucket.bucketName,
      description: 'Frontend S3 bucket name',
    });
  }
}
