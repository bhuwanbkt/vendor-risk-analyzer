# Larkspur Data Systems — Information Security Policy

**Document owner:** Chief Information Security Officer
**Version:** 4.2
**Effective date:** March 1, 2026
**Classification:** Customer confidential

> Larkspur Data Systems is a fictional company. This document is test material for vendor risk assessment software.

## 1. Purpose and Scope

This policy describes the security controls Larkspur Data Systems ("Larkspur") applies to its customer analytics platform, including production infrastructure, corporate systems, employees, and contractors.

## 2. Access Control

All workforce accounts are provisioned through a central identity provider using role-based access.

Multi-factor authentication (MFA) is required for all employee accounts accessing production systems and corporate email.

**Known exception:** As of the January 2026 internal audit, MFA is not enforced for 14 contractor accounts (approximately 12% of contractor accounts) that access the customer support console. Remediation is planned but has no committed completion date.

Privileged access to production is granted on a least-privilege basis and requires approval from an engineering manager.

User access reviews for production systems are performed **quarterly** by system owners.

## 3. Encryption

Customer data is encrypted at rest using AES-256 across all production databases, object storage, and backups.

All data in transit over public networks is protected with TLS 1.2 or higher. TLS 1.0 and 1.1 are disabled.

Encryption keys are managed in a cloud key management service. Keys are rotated annually, and access to key administration is restricted to the security engineering team.

## 4. Incident Response

Larkspur maintains a documented incident response plan that is reviewed annually.

Security incidents are triaged by the on-call security engineer within 1 hour of detection.

**Customer notification:** Larkspur will notify affected customers of a confirmed security breach involving their data **within 72 hours** of confirmation.

A post-incident review is completed for every severity-1 and severity-2 incident within 10 business days.

## 5. Vulnerability Management

Production systems are scanned for vulnerabilities weekly.

Remediation timelines:

- Critical vulnerabilities: within 7 days
- High vulnerabilities: within 30 days
- Medium vulnerabilities: within 90 days

An independent penetration test is performed annually. The most recent test was completed in November 2025.

**Open findings:** Two high-severity findings from the November 2025 penetration test (an authorization bypass in a reporting API and an outdated web server component) remain unresolved, exceeding the 30-day remediation timeline.

## 6. Business Continuity and Disaster Recovery

Larkspur maintains a business continuity plan with a recovery time objective (RTO) of 8 hours and a recovery point objective (RPO) of 1 hour for the analytics platform.

Production data is replicated to a secondary cloud region.

Disaster recovery exercises are required annually. **Evidence of a disaster recovery test for 2025 is not available**; the most recent documented exercise took place in 2023.

## 7. Data Retention and Deletion

Customer data is retained for the duration of the customer contract.

Upon contract termination, customer data is deleted from production systems within 30 days and from backups within 90 days. A deletion confirmation is provided on request.

## 8. Third-Party and Subprocessor Management

Larkspur uses the following subprocessors to deliver its service:

- A cloud infrastructure provider (hosting and storage)
- An email delivery provider (transactional email)
- A customer support ticketing provider

New subprocessors are assessed before onboarding. Larkspur requests current SOC 2 Type II reports from subprocessors annually.

**Gap:** Current SOC 2 reports have not been obtained for the email delivery provider and the customer support ticketing provider.

## 9. Privacy

Larkspur processes personal data only as a processor on behalf of its customers.

A Data Processing Agreement (DPA) aligned with GDPR is available to all customers. Larkspur supports customer requests to fulfil data subject access and deletion requests within 30 days.

Personal data is not sold or shared with third parties for advertising.

## 10. Policy Review

This policy is reviewed annually by the CISO and approved by executive leadership.
