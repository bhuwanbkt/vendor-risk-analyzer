# Brightwater Cloud Storage — Security Overview

**Prepared for:** Customer security reviews
**Version:** 2.1
**Date:** September 15, 2026

> Brightwater Cloud Storage is a fictional company. This document is test material for vendor risk assessment software.

## 1. Company and Service

Brightwater provides managed file storage and sharing for mid-sized businesses. Customer files are stored in two cloud regions in the United States.

## 2. Access Control

Single sign-on with SAML is available to all customers. Multi-factor authentication is enforced for all Brightwater employees and contractors.

Production access is limited to the site reliability team and is granted just-in-time for a maximum of 4 hours, with every session recorded.

Access reviews for production systems are performed quarterly.

## 3. Encryption

Customer files are encrypted at rest with AES-256. Customers on the Enterprise plan can supply their own encryption keys.

All connections use TLS 1.3. TLS 1.2 is accepted for legacy clients until December 31, 2026.

## 4. Incident Response

Brightwater maintains a 24/7 security operations function.

Customers are notified of a confirmed security incident affecting their data within 48 hours.

**Open issue:** The incident response plan has not been updated since 2023 and does not yet cover the second storage region added in 2025. An update is scheduled for Q1 2027.

## 5. Vulnerability Management

Automated vulnerability scanning runs daily. Critical vulnerabilities are remediated within 72 hours and high vulnerabilities within 14 days.

An independent penetration test was completed in July 2026 with no open high or critical findings.

## 6. Business Continuity

Files are replicated between both regions. The recovery time objective is 4 hours.

A full regional failover test was completed successfully in June 2026.

## 7. Data Retention

Deleted files are kept in a recoverable trash for 30 days and then permanently deleted. After contract termination, all customer data is deleted within 60 days.

## 8. Third Parties

Brightwater uses one infrastructure subprocessor and one payment processor. Current SOC 2 Type II reports are on file for both.

## 9. Privacy

A GDPR-aligned Data Processing Agreement is available. Brightwater does not access customer file contents except when a customer requests support.

## 10. Artificial Intelligence

Brightwater offers an optional AI search feature. **Customer files may be used to improve Brightwater's search models unless the customer opts out.** The feature is enabled by default for new accounts.
