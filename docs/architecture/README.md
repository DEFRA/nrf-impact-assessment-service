# NRF Impact Assessment Service - Documentation

[← Back to Main README](../README.md)

This directory contains comprehensive documentation for the NRF (Nature Restoration Fund) Impact Assessment Service.

## Table of Contents

- [Architecture Documentation](#architecture-documentation)
  - [LikeC4 Diagrams](#likec4-diagrams)
  - [How to Use the Diagrams](#how-to-use-the-diagrams)
  - [Maintaining the Diagrams](#maintaining-the-diagrams)

## Architecture Documentation

### LikeC4 Diagrams

The architecture is documented using [LikeC4](https://likec4.dev/), a domain-specific language for describing and visualising software architecture as code. The diagrams are stored in the `/docs/architecture/` directory.

#### Available Diagrams

##### 1. Logical Architecture (`architecture/base.c4`)

The logical architecture describes the system's high-level components, services, and their relationships.

**Key Elements:**

- **Actors**: Developer, LPA Planning Agent, NRF Operations, SSCL Finance Admin
- **External Systems**: GOV.UK Notify, GOV.UK Pay, Shared Oracle Platform (SOP)
- **NRF Services**:
  - Frontend Service (application submission, magic link authentication, file upload, payments)
  - Impact Assessment Service (geospatial analysis and levy calculation)
  - Verification Service (LPA agent portal with magic link authentication)
  - Invoice Requestor Service (invoice generation and SOP submission)
  - Reconciliation Service (bank transfer payment reconciliation)
  - Case Management Service (operations portal)

**Available Views:**

- `systemContext`: High-level view showing actors, NRF platform, and external integrations
- `index`: Complete system overview with all services and integrations
- `frontendService`: Developer-facing service components
- `impactAssessmentService`: Geospatial analysis workflow
- `verificationService`: LPA agent verification portal
- `invoiceRequestorService`: Invoice generation and submission
- `invoiceReconciliationService`: Payment reconciliation workflow
- `caseManagementService`: Operations management portal

##### 2. Deployment Architecture (`architecture/deployment.c4`)

The deployment architecture describes how the system is deployed in the AWS cloud environment, including networking, security, and infrastructure components.

**Key Elements:**

- **Production Environment** (`prod`): AWS eu-west-2 (London) region
- **Networking**: VPCs, subnets (public/private), Transit Gateway, NAT Gateways
- **Security**: WAF, AWS Shield, Secrets Manager, KMS encryption
- **Compute**: ECS Fargate services for all microservices
- **Storage**: RDS Aurora PostgreSQL, ElastiCache Redis, S3 buckets
- **Messaging**: SQS queues for asynchronous processing
- **CI/CD**: GitHub Actions and ECR

**Available Views:**

- `userInteractions`: User and engineer access patterns
- `cicdPipeline`: CI/CD workflow from GitHub to AWS ECS
- `completeInfrastructure`: Complete production infrastructure overview
- `networkArchitecture`: Network topology with VPCs and connectivity
- `securityArchitecture`: Security components (WAF, KMS, secrets, storage)
- `userFacingServices`: Public web services with user access
- `impactAssessmentDeployment`: Impact assessment service deployment
- `privateServicesView`: Backend services (Python workers, scheduled tasks)

### How to Use the Diagrams

#### Viewing with LikeC4

1. **Install LikeC4 CLI**:

   ```bash
   npm install -g likec4
   ```

2. **Preview the diagrams locally**:

   ```bash
   # From the project root
   cd docs/architecture

   # Start the LikeC4 server
   likec4 start
   ```

   This will open a browser window showing all available views with interactive navigation.

3. **Export diagrams**:

   ```bash
   # Export as PNG
   likec4 export png -o ./exports/

   # Export as SVG
   likec4 export svg -o ./exports/
   ```

#### Understanding the Diagram Structure

LikeC4 diagrams follow a hierarchical structure:

```
specification {
  // Define reusable element types and styles
}

model {
  // Define actors, systems, services, and relationships
}

deployment {
  // Define deployment environments and infrastructure
}

views {
  // Define what should be visible in each diagram
}
```

**Key Concepts:**

- **Elements**: Actors, systems, microservices, databases, queues, etc.
- **Relationships**: Directional arrows showing interactions between elements
- **Views**: Specific perspectives of the architecture (filtered subsets)
- **Styles**: Visual customisation (colours, shapes, icons)

### Maintaining the Diagrams

#### Best Practices

1. **Keep Diagrams in Sync with Code**

   - Update diagrams when new services are added or architecture changes
   - Review diagrams during architecture decision records (ADRs)
   - Include diagram updates in pull requests for architectural changes

2. **Use Meaningful Descriptions**

   - Add clear descriptions to all elements
   - Document the purpose of relationships with descriptive labels
   - Keep view descriptions concise but informative

3. **Organise Views Strategically**

   - Create focused views for specific audiences (developers, operations, security)
   - Avoid overly verbose views by using wildcards where appropriate
   - Maintain a system context view for high-level stakeholder communication

4. **Leverage Reusability**
   - Use `instanceOf` in deployment diagrams to link to logical components
   - Define styles in the specification for consistent visual language
   - Use wildcards (`*`, `**`) to include related elements efficiently

#### Common Maintenance Tasks

##### Adding a New Service

1. **Update Logical Architecture** (`base.c4`):

   ```
   // Add the new service system
   nrfNewService = system 'New Service' {
       description 'Service description'
       style {
           color green
       }

       microservice newService 'New Service' {
           description 'Detailed description'
           technology 'Technology stack'
       }

       // Add relationships
       newService -> postgresDatabase 'Interacts with database'
   }
   ```

2. **Add to Relevant Views**:

   ```
   view newServiceView {
       title 'New Service'
       description 'Service-specific view'
       include nrfNewService.*, postgresDatabase, ...
   }
   ```

3. **Update Deployment Architecture** (`deployment.c4`):
   ```
   // Add ECS service in the cluster
   ecsService newSvc 'New Service' {
       summary 'Service summary'
       technology 'Technology - ECS Fargate Service'
       instanceOf payForNRFService.nrfNewService.newService
   }
   ```

##### Updating Relationships

When data flows or integrations change:

1. Remove or update the old relationship in the model section
2. Add the new relationship with a descriptive label
3. Verify the change appears in relevant views
4. Update deployment architecture if infrastructure changes

##### Modifying Views

To reduce verbosity or consolidate views:

1. **Use Wildcards**:

   ```
   // Instead of listing all children
   include prod.aws.euWest2.nrfVPC.**

   // Instead of listing all relationships
   include element -> *
   ```

2. **Merge Similar Views**:
   - Identify overlapping content
   - Create a comprehensive view with a clear scope
   - Remove redundant views

#### Validation and Review

Before committing changes:

1. **Preview the diagrams**:

   ```bash
   likec4 start
   ```

2. **Check for common issues**:

   - Orphaned elements (defined but not included in any view)
   - Duplicate relationships
   - Inconsistent naming conventions
   - Missing descriptions

3. **Validate the syntax**:

   ```bash
   likec4 validate
   ```

4. **Review with stakeholders**:
   - Share the `systemContext` view for high-level discussions
   - Use service-specific views for detailed technical reviews
   - Leverage deployment views for infrastructure and security reviews

#### Versioning and History

- Architecture diagrams are version-controlled alongside code
- Use meaningful commit messages when updating diagrams
- Reference related issues or ADRs in commit messages
- Consider creating tags for major architectural milestones

## Additional Resources

- [LikeC4 Documentation](https://likec4.dev/docs)
- [LikeC4 Examples](https://likec4.dev/examples)
- [C4 Model](https://c4model.com/) - The underlying architecture documentation approach

---

[← Back to Main README](../README.md)
