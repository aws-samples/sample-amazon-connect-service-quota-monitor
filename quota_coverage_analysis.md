# Amazon Connect Quota Monitor - Coverage Analysis

## Executive Summary

**Analysis Date:** March 5, 2026  
**Current Implementation:** 70+ quotas monitored  
**Documentation Review:** AWS Connect Service Quotas (Latest)

## Coverage Status

### ✅ Well Covered Categories

1. **Core Amazon Connect** - Excellent coverage
   - Instance-level quotas (users, queues, routing profiles, etc.)
   - Account-level quotas (instances per account)
   - Resource limits (contact flows, prompts, phone numbers)

2. **Contact Handling & Metrics** - Good coverage
   - Concurrent calls, chats, tasks, emails
   - Campaign calls
   - Participant limits

3. **Routing & Queues** - Comprehensive
   - Queues per instance
   - Routing profiles
   - Quick connects
   - Hours of operation

4. **Integrations** - Good
   - Lambda functions
   - Lex bots
   - Application integrations

5. **Related Services** - Good
   - Customer Profiles (domains, object types, integrations)
   - Cases (domains, fields, templates)
   - Voice ID (domains, speakers)
   - Wisdom (knowledge bases, documents)

### ⚠️ Partially Covered Categories

1. **Contact Lens** - Minimal coverage
   - Currently has 4 entries
   - Missing: post-call analytics jobs, chat analytics jobs, summary jobs

2. **Tasks** - Basic coverage
   - Has task templates and fields
   - Missing: concurrent task limits monitoring via CloudWatch

3. **Forecasting & Capacity** - Limited
   - Only 4 quota entries
   - Missing: actual usage monitoring

### ❌ Critical Gap: API Rate Limits

**Current Status:** Only 2 API rate limit quotas monitored
**Required:** 100+ API throttling quotas need monitoring

## Missing API Rate Limits

### 1. Amazon Connect Core API (High Priority)

**Metrics APIs** (Critical for real-time monitoring):
- `GetMetricData` - Rate: 5 TPS, Burst: 8 TPS
- `GetMetricDataV2` - Rate: 10 TPS, Burst: 10 TPS  
- `GetCurrentMetricData` - Rate: 5 TPS, Burst: 8 TPS
- `GetCurrentUserData` - Rate: 5 TPS, Burst: 8 TPS

**Contact APIs** (High volume):
- `SearchContacts` - Rate: 0.5 TPS, Burst: 1 TPS
- `StartChatContact` - Rate: 5 TPS, Burst: 8 TPS
- `StartContactStreaming` - Rate: 5 TPS, Burst: 8 TPS
- `StopContactStreaming` - Rate: 5 TPS, Burst: 8 TPS
- `GetContactAttributes` - Rate: 10 TPS, Burst: 15 TPS
- `UpdateContactAttributes` - Rate: 10 TPS, Burst: 15 TPS
- `DescribeContact` - Rate: 10 TPS, Burst: 15 TPS
- `StopContact` - Rate: 10 TPS, Burst: 15 TPS
- `UpdateContact` - Rate: 10 TPS, Burst: 15 TPS
- `BatchPutContact` - Rate: 10 TPS, Burst: 15 TPS
- `TagContact` - Rate: 20 TPS, Burst: 25 TPS
- `UntagContact` - Rate: 20 TPS, Burst: 25 TPS
- `UpdateContactRoutingData` - Rate: 20 TPS, Burst: 20 TPS

**Integration APIs**:
- `SendChatIntegrationEvent` - Rate: 17 TPS, Burst: 26 TPS
- `SendIntegrationEvent` - Rate: 10 TPS, Burst: 15 TPS
- `CreateIntegrationAssociation` - Rate: 2 TPS (1 for SES_IDENTITY), Burst: 5 TPS
- `DeleteIntegrationAssociation` - Rate: 2 TPS, Burst: 5 TPS
- `ListIntegrationAssociations` - Rate: 25 TPS, Burst: 50 TPS

**Evaluation APIs**:
- All Evaluation actions - Rate: 1 TPS

**Participant APIs**:
- `CreateParticipant` - Rate: 5 TPS, Burst: 8 TPS
- `CreatePersistentContactAssociation` - Rate: 5 TPS, Burst: 8 TPS
- `UpdateParticipantRoleConfig` - Rate: 5 TPS, Burst: 8 TPS

### 2. Cases API (Medium Priority)

All with specific rate limits:
- `CreateCase` - Rate: 2 TPS, Burst: 10 TPS
- `SearchCases` - Rate: 2 TPS, Burst: 10 TPS
- `GetCase` - Rate: 4 TPS, Burst: 10 TPS
- `UpdateCase` - Rate: 2 TPS, Burst: 2 TPS
- `ListCasesForContact` - Rate: 2 TPS, Burst: 2 TPS
- `CreateField`, `ListFields`, `UpdateField` - Rate: 2 TPS, Burst: 5 TPS
- `BatchGetField` - Rate: 8 TPS, Burst: 25 TPS
- `ListFieldOptions` - Rate: 6 TPS, Burst: 16 TPS
- `GetTemplate`, `GetLayout` - Rate: 6 TPS, Burst: 20 TPS

### 3. Contact Lens API (Medium Priority)

- `ListRealtimeContactAnalysisSegments` - Rate: 1 TPS, Burst: 2 TPS
- `ListRealtimeContactAnalysisSegmentsV2` - Rate: 2 TPS, Burst: 5 TPS

### 4. Customer Profiles API (Medium Priority)

Over 30 APIs with varying limits:
- High-volume APIs (100 TPS): `SearchProfiles`, `ListProfileObjects`, `GetMatches`, `CreateProfile`, `UpdateProfile`, `PutProfileObject`
- Medium-volume (5-10 TPS): Domain and integration management
- Low-volume (1 TPS): Create/delete operations

### 5. Outbound Campaigns API (Medium Priority)

- Campaign management: 1 TPS (rate), 2 TPS (burst)
- Campaign state APIs: 5 TPS (rate), 10 TPS (burst)
- `DescribeCampaign`: 25 TPS (rate), 35 TPS (burst)
- Dialing APIs: 10 TPS (rate), 10 TPS (burst)

### 6. Participant Service API (High Priority for Chat)

- `CompleteAttachmentUpload` - Rate: 2 TPS, Burst: 5 TPS
- `CreateParticipantConnection` - Rate: 6 TPS, Burst: 9 TPS
- `DisconnectParticipant` - Rate: 3 TPS, Burst: 5 TPS
- `GetAttachment` - Rate: 8 TPS, Burst: 12 TPS
- `GetTranscript` - Rate: 8 TPS, Burst: 12 TPS
- `SendEvent`, `SendMessage` - Rate: 10 TPS, Burst: 15 TPS
- `StartAttachmentUpload` - Rate: 2 TPS, Burst: 5 TPS

### 7. Voice ID API (Low Priority - End of Support May 2026)

- `EvaluateSession` - 60 TPS
- Domain APIs - 2 TPS
- Speaker/Fraudster APIs - 5-10 TPS

### 8. Connect AI Agents API (New - Medium Priority)

Message template operations: 3-10 TPS depending on operation

## Monitoring Challenges for API Rate Limits

### Technical Challenges

1. **CloudWatch Metrics Availability**
   - Not all API calls publish metrics to CloudWatch
   - Some APIs don't expose usage metrics at all
   - Throttling events may not be visible until they occur

2. **Account-Level vs Instance-Level**
   - Most API quotas are account-level (shared across all instances)
   - Current monitoring is instance-focused
   - Need account-level aggregation

3. **Quota Code Availability**
   - Many API rate limits don't have L-codes (quota codes)
   - Service Quotas API may not return them
   - Hard to request increases programmatically

4. **Real-Time Monitoring**
   - API throttling happens in real-time
   - CloudWatch metrics have delays
   - Need different monitoring approach

### Recommended Monitoring Approach

#### Option 1: CloudWatch API Metrics (Partial Coverage)
```
Namespace: AWS/Connect
Metric: APICallCount
Dimensions: Operation, InstanceId (where applicable)
```

**Pros:**
- Uses existing CloudWatch infrastructure
- Automated metric collection
- Historical trend analysis

**Cons:**
- Not all APIs publish metrics
- Only shows usage, not limits
- Doesn't show burst usage
- May have delays

#### Option 2: CloudWatch Logs Insights (More Comprehensive)
```
Query CloudTrail logs for:
- API call counts per minute
- Throttling exceptions (ThrottlingException)
- Rate limit warnings
```

**Pros:**
- Comprehensive coverage of all APIs
- Can detect throttling events
- Fine-grained timing

**Cons:**
- Requires CloudTrail enabled
- Higher cost
- More complex queries
- Performance overhead

#### Option 3: Service Quotas API (Limited)
```
Use GetServiceQuota API to fetch:
- Current quota values
- Applied quotas
- Usage metrics (where available)
```

**Pros:**
- Official quota information
- Easy to request increases
- Programmatic access

**Cons:**
- Not all API limits have quota codes
- Limited usage metrics
- No real-time data

#### Option 4: Hybrid Approach (Recommended)

1. **For Critical APIs** (GetMetricData, StartChatContact, etc.):
   - Use CloudWatch API metrics where available
   - Set up alarms at 80% of rate limit
   - Monitor over 1-minute windows

2. **For All APIs**:
   - Enable CloudTrail logging
   - Create CloudWatch Logs metric filters for ThrottlingException
   - Alert on any throttling occurrences

3. **For Quota Tracking**:
   - Use Service Quotas API to fetch current limits
   - Store in monitoring system
   - Update weekly/monthly

## Missing Resource Quotas

### 1. New/Recent Quotas (from 2024-2026)

- **Email capabilities** (recently added):
  - Email addresses per instance: 100 (adjustable to 500)
  - Email domains per instance: 1 Connect + 100 custom
  - Email addresses per message: 50 total
  - Active email contact expiry: 14-90 days
  - Active email conversation expiry: 90 days
  - Concurrent active emails: 1000 (CloudWatch: ConcurrentEmails)

- **Agent proficiencies**:
  - Proficiencies per agent: 10

- **Custom metrics**:
  - Custom metrics per instance: 1000

- **Predefined attributes**:
  - Predefined attributes per instance: 150

### 2. Contact Lens Quotas

Need better monitoring for:
- Concurrent post-call analytics jobs: 200
- Concurrent chat analytics jobs: 200
- Concurrent automated interaction analytics jobs: 20
- Post-contact summary jobs: 10
- After-call summary jobs: 2

### 3. Evaluation Quotas

- Evaluation questions with Ask AI: 10 per contact
- Automated evaluation questions: 10 per contact

## Recommendations

### Immediate Actions (High Priority)

1. **Add Critical API Monitoring**
   - Implement CloudWatch-based monitoring for top 20 most-used APIs
   - Focus on: GetMetricData, StartChatContact, GetCurrentMetricData
   - Set up alerts at 80% of rate limits

2. **Enable Throttling Detection**
   - Create CloudWatch Logs metric filter for ThrottlingException
   - Alert operations team on any API throttling
   - Dashboard showing throttled API calls

3. **Add Email Quotas**
   - Monitor concurrent active emails (CloudWatch metric exists)
   - Track email addresses and domains per instance
   - Alert at 80% utilization

### Medium-Term Actions

4. **Expand Contact Lens Monitoring**
   - Monitor concurrent analytics jobs
   - Track post-contact summary usage
   - Alert on job queue buildup

5. **Add Cases API Monitoring**
   - Implement for customers using Cases
   - Focus on high-volume APIs (SearchCases, GetCase)

6. **Customer Profiles Enhancement**
   - Monitor high-volume operations (SearchProfiles, CreateProfile)
   - Track integration limits

### Long-Term Enhancements

7. **Comprehensive API Dashboard**
   - Centralized view of all API usage
   - Rate limit proximity indicators
   - Historical trending

8. **Automated Quota Increase Requests**
   - Detect consistent high utilization (>70%)
   - Automatically prepare quota increase requests
   - Integration with Service Quotas API

9. **Predictive Analytics**
   - ML-based prediction of quota breaches
   - Growth trend analysis
   - Capacity planning recommendations

## Implementation Priority Matrix

| Category | Priority | Effort | Impact | Recommendation |
|----------|----------|--------|--------|----------------|
| Critical API rate limits (top 20) | 🔴 High | Medium | High | Implement immediately |
| Throttling exception detection | 🔴 High | Low | High | Implement immediately |
| Email quota monitoring | 🟡 Medium | Low | Medium | Next sprint |
| Contact Lens job monitoring | 🟡 Medium | Medium | Medium | Next sprint |
| Cases API monitoring | 🟡 Medium | Low | Low-Medium | If Cases is used |
| Customer Profiles API | 🟢 Low | Medium | Low-Medium | If Profiles is used |
| Outbound Campaigns API | 🟢 Low | Low | Low | If Campaigns is used |
| Voice ID API | 🟢 Low | Low | Very Low | End of support 5/2026 |

## Code Implementation Status

### Current Code Structure
```python
ENHANCED_CONNECT_QUOTA_METRICS = {
    # 70+ quota definitions
    # Categories: CORE_CONNECT, CONTACT_HANDLING, ROUTING_QUEUES, etc.
}
```

### What's Working Well
- Dynamic instance discovery
- Multiple monitoring methods (api_count, cloudwatch, service_quotas)
- Consolidated alerting
- Flexible storage (S3, DynamoDB)
- Multi-service client management

### What Needs Enhancement
1. **API rate limit monitoring method** - New method needed
2. **Account-level aggregation** - Currently instance-focused
3. **Throttling detection** - Not currently implemented
4. **Email metrics** - Need CloudWatch metric integration
5. **Real-time alerting** - API throttling needs immediate alerts

## Conclusion

The current implementation provides excellent coverage of **resource-based quotas** (users, queues, instances, etc.) but has a **critical gap in API rate limit monitoring**.

**Key Statistics:**
- ✅ Resource quotas: ~70 monitored (excellent)
- ❌ API rate limits: ~2 monitored (critical gap)
- 📊 Total documented API limits: ~100+

**Risk Assessment:**
- **High Risk**: Production systems hitting API rate limits without warning
- **Impact**: Throttled API calls, degraded customer experience, failed operations
- **Likelihood**: High for customers with heavy API usage or integrations

**Next Steps:**
1. Implement throttling exception monitoring (quick win)
2. Add CloudWatch-based monitoring for top 20 APIs
3. Create API usage dashboard
4. Add email quota monitoring
5. Expand Contact Lens monitoring