# Using VoltVerse with AWS Workshop Studio

Workshop Studio can provision every participant account with this stack. The pattern:

1. Package the source as a workshop asset:
   ```bash
   scripts/package-source.sh            # creates dist/voltverse-source.zip
   cp dist/voltverse-source.zip <workshop-repo>/assets/
   cp cloudformation/voltverse.yaml <workshop-repo>/static/voltverse.yaml
   ```
2. Reference the template in `contentspec.yaml`. Pass the asset bucket and the participant role. The example below uses Workshop Studio's documented template variables. **Check the variable names against the current Workshop Studio authoring guide before your event.**

```yaml
version: 2.0

defaultLocaleCode: en-US
localeCodes: [en-US]

awsAccountConfig:
  accountSources: [WorkshopStudio]
  participantRole:
    managedPolicies:
      - "arn:aws:iam::aws:policy/ReadOnlyAccess"
      - "arn:aws:iam::aws:policy/CloudWatchFullAccessV2"
      - "arn:aws:iam::aws:policy/AWSXrayReadOnlyAccess"
  regionConfiguration:
    minAccessibleRegions: 1
    maxAccessibleRegions: 1
    accessibleRegions:
      required: [us-west-2]

infrastructure:
  cloudformationTemplates:
    - templateLocation: static/voltverse.yaml
      label: VoltVerse store
      parameters:
        - templateParameter: SourceS3Bucket
          defaultValue: "{{.AssetsBucketName}}"
        - templateParameter: SourceS3Key
          defaultValue: "{{.AssetsBucketPrefix}}voltverse-source.zip"
        - templateParameter: ParticipantRoleArn
          defaultValue: "{{.ParticipantRoleArn}}"
        - templateParameter: EnablePublicClusterEndpoint
          defaultValue: "true"
```

Notes:
- The deployer's IAM policy grants `s3:GetObject` only on `SourceS3Bucket/SourceS3Key`. If the asset bucket is in another account, its bucket policy must allow the participant account to read it. Workshop Studio sets this up for its asset bucket.
- With `EnablePublicClusterEndpoint=true` and no `PublicEndpointCidrs`, the IAM-authenticated EKS API is reachable from any IP. Only the participant role and the deployer have access entries. Set `PublicEndpointCidrs` to narrow it.
- Bedrock model access (the Anthropic use-case form) must be in place in the participant accounts. Plan this with your event's account provisioning.
- Provisioning takes about 30 minutes, so allow for it in the event schedule.
