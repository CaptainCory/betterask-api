# Privacy and Data Management Features

BetterAsk now includes comprehensive privacy and data management features that comply with modern data protection standards like GDPR and CCPA.

## 🔒 New Endpoints

### 1. DELETE /profile/{human_id}
**Right to be Forgotten** - Complete data deletion

```http
DELETE /profile/{human_id}
X-API-Key: your_api_key
```

**What it does:**
- Permanently deletes the human profile from `human_profiles` table
- Removes all associated question performance data from `question_performance` table
- Returns confirmation of what was deleted
- Logs the deletion for audit purposes

**Response:**
```json
{
  "success": true,
  "human_id": "user123",
  "deleted": {
    "profile": true,
    "question_performance_records": 15,
    "total_records_deleted": 16
  },
  "message": "All data for this human has been permanently deleted."
}
```

### 2. GET /privacy/{human_id}
**Data Transparency** - Audit what data is stored

```http
GET /privacy/{human_id}
X-API-Key: your_api_key
```

**What it reveals:**
- When the profile was created and last updated
- How many questions have been asked
- Which life domains are covered and to what depth
- Number of conversation history entries
- Data categories stored (without revealing sensitive content)
- Links to privacy policy and data management options

**Response:**
```json
{
  "human_id": "user123",
  "profile_created": "2024-01-15T10:30:00Z",
  "profile_last_updated": "2024-01-20T15:45:00Z",
  "data_summary": {
    "domains_covered": {
      "count": 5,
      "domains": ["Career Direction", "Relationship Quality", "Health & Body"]
    },
    "questions_asked": 12,
    "conversation_history_entries": 8,
    "question_performance_records": 15,
    "data_categories": [
      {"category": "conversation_history", "type": "list", "size": 8},
      {"category": "interests", "type": "list", "size": 3}
    ],
    "understanding_score": 0.42
  },
  "privacy_policy": "https://betterask.dev/privacy",
  "data_portability": "Request full export via POST /profile/user123/export",
  "right_to_be_forgotten": "Delete all data via DELETE /profile/user123"
}
```

### 3. POST /profile/{human_id}/export
**Data Portability** - Download all stored data

```http
POST /profile/{human_id}/export
X-API-Key: your_api_key
```

**What it includes:**
- Complete profile data (known_data, domains, questions asked)
- All conversation history
- Question performance analytics
- Understanding scores and domain analysis
- Timestamps and metadata

**Response:**
```json
{
  "success": true,
  "export_data": {
    "human_id": "user123",
    "export_timestamp": "2024-01-20T16:00:00Z",
    "profile": {
      "created_at": "2024-01-15T10:30:00Z",
      "updated_at": "2024-01-20T15:45:00Z",
      "total_questions": 12,
      "known_data": { /* all structured data */ },
      "domains_covered": ["career_direction", "relationship_quality"],
      "domains_depth": {"career_direction": 7, "relationship_quality": 5},
      "questions_asked": ["What drives you at work?", "..."],
      "gaps_history": [/* gap targeting history */]
    },
    "analytics": {
      "understanding_score": 0.42,
      "domains_analysis": { /* detailed domain insights */ }
    },
    "question_performance": [/* all performance data */]
  },
  "data_portability_notice": "This is your complete data export from BetterAsk...",
  "format": "JSON"
}
```

## 🛡️ Privacy Headers

All API responses now include privacy headers:

### X-Privacy-Policy
Every response includes a link to the privacy policy:
```
X-Privacy-Policy: https://betterask.dev/privacy
```

### X-Data-Stored
For `/ask` and `/learn` endpoints, indicates whether data was persisted:
```
X-Data-Stored: true   // when human_id is provided
X-Data-Stored: false  // for stateless requests
```

This helps agents understand when their requests are storing data vs. being processed stateless.

## 🔐 Authentication

All privacy endpoints use the same API key authentication as existing profile endpoints:

- Requires valid API key via `X-API-Key` header
- Each agent can only access/delete profiles associated with their API key
- Built-in isolation prevents cross-agent data access

## 📊 Data Categories

BetterAsk stores the following types of data:

### Human Profiles (`human_profiles` table)
- `human_id` - identifier provided by the agent
- `agent_api_key` - which agent owns this profile
- `known_data` - JSON blob of structured knowledge about the human
- `domains_covered` - which life domains have been explored
- `domains_depth` - depth scores for each domain (0-10)
- `questions_asked` - history of questions asked
- `gaps_history` - history of knowledge gaps targeted
- `total_questions` - count of questions asked
- `created_at` / `updated_at` - timestamps

### Question Performance (`question_performance` table)
- `question_text` - the actual question asked
- `question_source` - corpus, generated, etc.
- `gap_targeted` - which knowledge gap was being filled
- `vectors_used` - which question vectors were employed
- `understanding_delta` - how much understanding improved
- `answer_depth` - how deep the human's response was
- `domain_explored` - which life domain was explored
- `conversation_depth` - how many questions deep in the conversation
- `human_context_summary` - anonymized context (contains human_id)
- `agent_role` - what role the agent was playing
- `created_at` - when the question was asked

## 🎯 Use Cases

### For End Users (Humans)
- **Transparency**: "What does my AI assistant know about me?"
- **Data Export**: "I want to download all my data to use with another service"
- **Right to be Forgotten**: "Delete everything you know about me"

### For AI Agents
- **Privacy Compliance**: Automatically handle data subject requests
- **Data Minimization**: Know when you're storing data vs. processing stateless
- **Audit Trail**: Track what data you've collected and when

### For Developers
- **Compliance**: Built-in GDPR/CCPA compliance features
- **Transparency**: Clear visibility into data storage patterns
- **Data Governance**: Structured approach to data retention and deletion

## 🚀 Implementation Details

### Database Schema
No changes to existing schema. Privacy features work with current tables:
- `human_profiles` - main profile storage
- `question_performance` - analytics and learning data

### Error Handling
- 404 responses for non-existent profiles
- Proper authentication on all endpoints
- Comprehensive logging for audit trails

### Performance
- Efficient queries using existing indexes
- Minimal overhead on existing endpoints
- Privacy headers add negligible latency

## 📋 Testing

Run the test script to verify functionality:

```bash
cd /path/to/betterask-api
python3 test_privacy.py
```

The test script verifies:
1. Privacy audit endpoint behavior
2. Profile export functionality  
3. Profile deletion capabilities
4. Privacy headers on data endpoints

## 🔄 Migration

No migration required. Privacy features work with existing data:
- Existing profiles immediately support privacy endpoints
- Historical question performance data is included in exports
- All privacy features respect existing API key isolation

## 📝 Next Steps

Consider implementing:
- **Data Retention Policies**: Automatic deletion of old data
- **Consent Management**: Track what users have consented to
- **Data Anonymization**: Options to anonymize instead of delete
- **Audit Logging**: More detailed logs of data access and changes
- **Privacy Dashboard**: Web interface for users to manage their data

## 🔗 Related Documentation

- [BetterAsk API Documentation](./README.md)
- [Privacy Policy](https://betterask.dev/privacy) 
- [Data Processing Agreement](https://betterask.dev/dpa)