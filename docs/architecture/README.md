# NRF Impact Assessment Service - Documentation

[← Back to Main README](../README.md)

This directory contains comprehensive documentation for the NRF (Nature Restoration Fund) Impact Assessment Service.

## Table of Contents

- [Architecture Documentation](#architecture-documentation)
  - [LikeC4 Diagrams](#likec4-diagrams)
  - [Sequence Diagrams](#sequence-diagrams)
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

### Sequence Diagrams (Dynamic Views)

The system's user flows and interactions are documented using LikeC4 **dynamic views**. These are sequence diagrams that show the temporal flow of interactions between actors, services, and external systems. Dynamic views are integrated directly into the architecture model, ensuring consistency between static and dynamic representations.

#### Available Dynamic Views (`architecture/base.c4`)

All sequence diagrams are defined as dynamic views in the `base.c4` file. The following flows are documented:

**1. Application Submission Flow** (`applicationSubmission`)

Shows the complete flow for a developer submitting a planning application (all applications follow the same submission process):

- Magic link authentication via GOV.UK Notify
- Session caching in Redis
- File upload with virus scanning (quarantine → scan → tenant bucket)
- Application data persistence
- Queueing for impact assessment
- Note: All applications receive invoices after verification (see Invoice Generation flow)
- Note: Payment method (credit card or bank transfer) is determined by invoice amount

**2. Impact Assessment Processing Flow** (`impactAssessmentProcessing`)

Demonstrates the asynchronous processing of geospatial analysis:

- Worker polling SQS queue for new applications
- Reading geospatial and biodiversity data from PostgreSQL
- Calculating impact assessment and levy estimate
- Saving results to database
- Sending levy estimate notifications to developers

**3. LPA Agent Verification Flow** (`lpaVerification`)

Shows how Local Planning Authority agents verify applications:

- Magic link authentication
- Session management with Redis
- Browsing pending applications
- Reviewing application details and documents
- Updating verification status

**4. Invoice Generation and Submission Flow** (`invoiceGeneration`)

Demonstrates the scheduled invoice generation process (for ALL applications):

- Querying verified applications requiring invoices
- Generating and formatting invoice data
- Submitting invoices to SOP via FTP
- Marking invoice as submitted in database
- SSCL admin approval in SOP
- Invoice delivery to developers:
  - Applications < £10k: Invoice with GOV.UK Pay link for credit card payment
  - Applications > £10k: Invoice with bank details for BACS transfer

**5. Payment Reconciliation Flow** (`paymentReconciliation`)

Shows how bank transfer payments are reconciled (> £10k) - detailed background process:

- Developer paying invoice via bank transfer
- SOP matching payments to invoices
- Reconciliation service retrieving transaction reports via FTP
- Matching payments to applications in PostgreSQL
- Updating application status to "Completed"
- Sending payment confirmation notifications

**6. Payment Flow - Credit Card via GOV.UK Pay** (`paymentCreditCard`)

Shows how developers pay via credit card (< £10k) using the link in their invoice:

- Developer receives invoice from SOP with GOV.UK Pay link
- Developer clicks link and enters card details
- Payment processing via GOV.UK Pay
- Webhook callback to frontend service on success
- Database status updates (payment and application marked as Completed)
- Payment confirmation email sent to developer

**7. Payment Flow - Bank Transfer** (`paymentBankTransfer`)

Shows how developers pay via BACS bank transfer (> £10k):

- Developer receives invoice from SOP with bank details
- Developer initiates BACS payment through their bank
- Bank statement data transferred to SOP
- SOP exports transaction report via FTP
- Reconciliation service retrieves and processes payment data
- Application status updated to "Completed"
- Payment confirmation email sent to developer

**8. Case Management Flow** (`caseManagement`)

Shows NRF operations team managing cases, disputes, and exceptions:

- Operations portal access
- Dashboard with case overview
- Case detail viewing and updates
- Notification delivery to developers

**9. Complete End-to-End Flow - Credit Card Payment** (`endToEndFlowCreditCard`)

High-level overview showing the entire journey for applications paid via credit card (< £10k):

- Application submission and file upload
- Impact assessment and levy calculation
- LPA verification
- Invoice generation with GOV.UK Pay link
- Credit card payment via link in invoice
- Payment confirmation
- Includes all key actors: Developer, LPA Agent, SSCL Finance Admin

**10. Complete End-to-End Flow - Bank Transfer Payment** (`endToEndFlowBankTransfer`)

High-level overview showing the entire journey for applications paid via bank transfer (> £10k):

- Application submission and file upload
- Impact assessment and levy calculation
- LPA verification
- Invoice generation with bank details
- BACS bank transfer payment
- Payment reconciliation
- Payment confirmation
- Includes all key actors: Developer, LPA Agent, SSCL Finance Admin

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

The dynamic views (sequence diagrams) are integrated into the LikeC4 diagrams and can be viewed using the same LikeC4 tools described above. When you run `likec4 start`, you'll see both static views and dynamic (sequence) views in the navigation.

#### Understanding the Diagram Structure

**LikeC4 Diagrams**

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

**LikeC4 Dynamic Views (Sequence Diagrams)**

LikeC4 dynamic views use a simple text-based syntax to describe interactions over time:

```
dynamic view flowName {
    title 'Flow Title'
    description 'Flow description'

    actor -> service 'Action description'
    service -> database 'Query data'
    database -> service 'Return results'
    service -> actor 'Response'
}
```

**Key Concepts:**

- **Participants**: References to actors, services, databases, queues defined in the model
- **Arrows**: `->` showing interactions between participants
- **Labels**: Descriptive text explaining each interaction
- **Sequential flow**: Interactions are shown in the order they're defined
- **Comments**: Use `//` for inline comments to annotate complex flows

### Maintaining the Diagrams

#### Best Practices

1. **Keep Diagrams in Sync with Code**

   - Update diagrams when new services are added or architecture changes
   - Update dynamic views when user flows or integrations change
   - Review diagrams during architecture decision records (ADRs)
   - Include diagram updates in pull requests for architectural changes

2. **Use Meaningful Descriptions**

   - Add clear descriptions to all elements and views
   - Document the purpose of relationships with descriptive labels
   - Keep view descriptions concise but informative
   - Use descriptive labels for dynamic view interactions
   - Add comments to clarify complex logic or business rules in flows

3. **Organise Views Strategically**

   - Create focused static views for specific audiences (developers, operations, security)
   - Avoid overly verbose views by using wildcards where appropriate
   - Maintain a system context view for high-level stakeholder communication
   - Create one dynamic view per user flow to keep sequence diagrams focused
   - Group related flows together in the views section

4. **Leverage Reusability**

   - Use `instanceOf` in deployment diagrams to link to logical components
   - Define styles in the specification for consistent visual language
   - Use wildcards (`*`, `**`) to include related elements efficiently in static views
   - Reference the same model elements across static and dynamic views
   - Use consistent participant references across dynamic views

5. **Version Control Best Practices**
   - LikeC4 diagrams (including dynamic views) are text-based, making them git-friendly
   - Commit diagram changes alongside related code changes
   - Use meaningful commit messages that reference the architectural change
   - Review diagram diffs in pull requests to catch unintended changes

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

4. **Update Dynamic Views** (in `base.c4`):

If the new service participates in user flows, add it to relevant dynamic views:

```
// In an existing or new dynamic view
dynamic view newServiceFlow {
    title 'New Service Flow'
    description 'Flow description'

    frontend -> newService 'Call new service'
    newService -> postgresDatabase 'Query data'
    postgresDatabase -> newService 'Return data'
    newService -> frontend 'Response'
}
```

##### Updating Relationships

When data flows or integrations change:

1. **Update LikeC4 diagrams**:

   - Remove or update the old relationship in the model section
   - Add the new relationship with a descriptive label
   - Verify the change appears in relevant views
   - Update deployment architecture if infrastructure changes

2. **Update dynamic views**:
   - Locate the affected user flow dynamic view(s)
   - Update interaction flows to reflect the new pattern
   - Add or remove participants as needed
   - Update interaction labels to reflect the new behaviour

##### Adding or Modifying User Flows

When adding a new user flow or significantly changing an existing one:

1. **Create or update dynamic view** (in `base.c4`):

   - Add a new `dynamic view` block in the views section
   - Use a descriptive name and title that clearly identifies the flow
   - Include all relevant participants (actors, services, external systems)
   - Document the complete interaction sequence with clear labels
   - Add comments to explain business rules or complex logic

2. **Update README**:
   - Add the new flow to the "Available Dynamic Views" section
   - Provide a brief description of what the flow demonstrates
   - List key steps or decision points

Example:

```
dynamic view newUserFlow {
    title 'New User Flow Description'
    description 'Detailed description of what this flow demonstrates'

    // User initiates action
    user -> serviceA 'Initiate action'
    serviceA -> database 'Query data'
    database -> serviceA 'Return results'

    // Process based on results
    serviceA -> serviceB 'Process request'
    serviceB -> database 'Update records'
    database -> serviceB 'Confirm update'
    serviceB -> serviceA 'Processing complete'
    serviceA -> user 'Confirmation message'
}
```

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
