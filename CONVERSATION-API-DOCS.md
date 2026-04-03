# BetterAsk Conversation Mode API Documentation

## Base URL
```
https://betterask.dev
```

## Authentication
All conversation endpoints require an API key in the header:
```
x-api-key: ba_live_your_key_here
```

## Endpoints

### 1. Start Conversation Session

**POST** `/session/start`

Start a new multi-turn conversation session.

**Request Headers:**
```
x-api-key: ba_live_your_key
Content-Type: application/json
```

**Request Body:**
```json
{
  "context": "discovery",           // Optional. Default: "discovery"
  "human_id": "user_123",          // Optional. For persistence across sessions
  "session_length": 7,             // Optional. Default: 7. Range: 1-20
  "starting_vectors": ["specificity", "permission"]  // Optional. Override warm start
}
```

**Response:**
```json
{
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "question": {
    "question": "What's something you're excited about right now that you wish more people asked you about?",
    "follow_up": "What makes that particularly meaningful to you?",
    "vectors": ["specificity", "permission"],
    "vector_names": ["Specificity", "Permission to be Real"],
    "gap_targeted": "self_expression",
    "why": "Opening question to build rapport and establish conversation baseline",
    "what_to_listen_for": "Interests, values, communication style"
  },
  "question_number": 1,
  "total_planned": 7,
  "strategy": "warm_start"
}
```

**Error Responses:**
- `400` - Invalid request parameters
- `401` - Invalid or missing API key  
- `429` - Rate limit exceeded (max 3 active sessions per key)

---

### 2. Answer Question & Get Next

**POST** `/session/answer`

Process a user's answer and receive the next strategic question.

**Request Headers:**
```
Content-Type: application/json
```

**Request Body:**
```json
{
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "answer": "I've been working on a side project that helps small businesses automate their bookkeeping. It's exciting because I can see how much time it saves them, but I don't usually talk about it because people's eyes glaze over when I mention accounting software."
}
```

**Response:**
```json
{
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "insight": {
    "revealed": [
      "Working on automation project for small businesses",
      "Motivated by helping others save time", 
      "Self-aware about others' lack of interest in technical details",
      "Tends to downplay achievements due to perceived boring subject matter"
    ],
    "avoided": [
      "Specific technical details about the project",
      "How they learned to build software"
    ],
    "contradictions": [],
    "depth_score": 7.5,
    "themes_identified": ["helping_others", "technical_skills", "social_awareness"]
  },
  "next_question": {
    "question": "What's the moment you realized how much time you could actually save people?", 
    "follow_up": "How did that change how you thought about your work?",
    "vectors": ["time", "specificity", "perspective_shift"],
    "vector_names": ["Time", "Specificity", "Perspective Shift"],
    "gap_targeted": "impact_awareness",
    "why": "Following thread about helping others - asking for specific moment of realization",
    "what_to_listen_for": "Origin story, emotional connection to impact, values about helping"
  },
  "question_number": 2,
  "vectors_engaged": ["specificity", "permission", "time", "perspective_shift"],
  "vectors_untouched": ["confession", "comparison", "hypothetical"],
  "conversation_depth": "deepening"
}
```

**Rate Limiting:**
- Maximum 1 answer per 5 seconds per session

**Error Responses:**
- `400` - Invalid session_id or session not active
- `404` - Session not found
- `429` - Rate limit exceeded (1 answer per 5 seconds)

---

### 3. Get Conversation Summary

**GET** `/session/{session_id}/summary`

Retrieve comprehensive insights and personality analysis from the conversation.

**Request Headers:**
```
x-api-key: ba_live_your_key  // Optional but recommended
```

**Response:**
```json
{
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "session_status": "complete",
  "questions_answered": 7,
  "duration_minutes": 23.5,
  "structural_insights": [
    "Strong pattern of building tools that help others while downplaying personal achievements",
    "Values practical impact over recognition or glamour", 
    "Demonstrates high social awareness and empathy in professional contexts",
    "Tends to assume others won't find their work interesting, possibly undervaluing their contributions"
  ],
  "ephemeral_insights": [
    "Currently energized by positive user feedback on bookkeeping project",
    "Recently had realization about time-saving potential of automation"
  ],
  "personality_sketch": "This person operates with a fascinating combination of technical skill and social sensitivity. They're drawn to building solutions that genuinely help people, particularly in areas others might find mundane or tedious. There's a consistent pattern of creating meaningful impact while maintaining remarkable humility about their work. They possess strong empathy and social awareness, often anticipating others' reactions and adjusting their communication accordingly. This sensitivity, while a strength in building user-focused products, may also lead them to undervalue their own expertise and contributions. They seem most energized when they can see direct, practical benefits for others, suggesting that purpose and impact matter more to them than recognition or technical prestige.",
  "vectors_engaged": {
    "specificity": 9.2,
    "permission": 8.1,
    "time": 7.8,
    "perspective_shift": 7.3,
    "confession": 6.2,
    "other_eyes": 5.9
  },
  "vectors_avoided": {
    "hypothetical": 1.2,
    "comparison": 0.8,
    "subversion": 0.3
  },
  "suggested_followup": [
    "What would change if you let people see how proud you are of your technical work?",
    "When did you first realize you had a talent for understanding what people actually need?",
    "What's the most ambitious version of this automation vision that you don't usually admit to having?"
  ],
  "conversation_quality": {
    "engagement_score": 8.7,
    "depth_achieved": 7.9,
    "breakthrough_moments": 2,
    "avoidance_instances": 3
  }
}
```

**Error Responses:**
- `404` - Session not found
- `400` - Session has no conversation data

---

## Available Contexts

When starting a session, you can specify a `context` to optimize the conversation strategy:

- `"onboarding"` - Meeting this person for the first time
- `"discovery"` - Understanding needs, pain points, goals (default)  
- `"coaching"` - Helping person grow and self-reflect
- `"rapport"` - Pure connection-building and relationship development
- `"assessment"` - Evaluating capabilities, personality, or fit
- `"interview"` - Structured conversation for experience/perspective
- `"content"` - Generating questions for social media or publications

## Vector System

BetterAsk uses 21 question vectors that create different types of engagement:

**Warm Start Vectors** (Questions 1-2):
- `specificity` - Concrete, grounded questions
- `name_an_example` - "Give me an instance of..."  
- `permission` - Safe space for authentic sharing

**Deep Dive Vectors** (Questions 3-5):
- `confession` - Vulnerable truth-telling
- `perspective_shift` - "How would X see this?"
- `other_eyes` - External viewpoint questions
- `contradiction` - Exploring tensions and paradoxes

**Reflective Vectors** (Questions 6-7):
- `time` - Past/future temporal questions
- `trajectory` - Direction and change over time
- `cumulation` - Adding up life experiences

## Session Management

### Session Lifecycle
1. **Active** - Session can accept answers
2. **Complete** - All planned questions answered  
3. **Abandoned** - Session expired (24 hours) without completion

### Limits
- **Active sessions per API key**: 3 maximum
- **Session duration**: 24 hours before auto-expiration
- **Answer rate**: 1 per 5 seconds per session
- **Session length**: 1-20 questions (default: 7)

### Cleanup
Sessions automatically expire and clean up after 24 hours.

## Error Codes

| Code | Meaning | Common Causes |
|------|---------|---------------|
| 400 | Bad Request | Invalid session_id, malformed JSON |
| 401 | Unauthorized | Missing or invalid API key |
| 404 | Not Found | Session doesn't exist |
| 429 | Rate Limited | Too many sessions or answers too fast |
| 500 | Server Error | LLM failure, database issues |

## SDKs and Examples

### JavaScript/Node.js
```javascript
const BetterAskConversation = require('@betterask/conversation');

const conversation = new BetterAskConversation({
  apiKey: 'ba_live_your_key',
  baseUrl: 'https://betterask.dev'
});

// Start conversation
const session = await conversation.start({
  context: 'discovery',
  humanId: 'user_123'
});

console.log('First question:', session.question.question);

// Answer question  
const response = await conversation.answer({
  sessionId: session.sessionId,
  answer: 'I love building tools that help small businesses save time.'
});

console.log('Insights:', response.insight.revealed);
console.log('Next question:', response.nextQuestion.question);

// Get summary when complete
const summary = await conversation.getSummary(session.sessionId);
console.log('Personality sketch:', summary.personalitySketch);
```

### Python
```python
import requests

class BetterAskConversation:
    def __init__(self, api_key, base_url="https://betterask.dev"):
        self.api_key = api_key
        self.base_url = base_url
        self.headers = {"x-api-key": api_key, "Content-Type": "application/json"}
    
    def start_session(self, context="discovery", human_id=None, session_length=7):
        response = requests.post(
            f"{self.base_url}/session/start",
            headers=self.headers,
            json={"context": context, "human_id": human_id, "session_length": session_length}
        )
        return response.json()
    
    def answer_question(self, session_id, answer):
        response = requests.post(
            f"{self.base_url}/session/answer",
            headers={"Content-Type": "application/json"},
            json={"session_id": session_id, "answer": answer}
        )
        return response.json()
    
    def get_summary(self, session_id):
        response = requests.get(f"{self.base_url}/session/{session_id}/summary")
        return response.json()

# Usage
conv = BetterAskConversation("ba_live_your_key")
session = conv.start_session(context="discovery")
```

### cURL Examples
```bash
# Start session
curl -X POST "https://betterask.dev/session/start" \
  -H "x-api-key: ba_live_your_key" \
  -H "Content-Type: application/json" \
  -d '{"context": "discovery", "session_length": 5}'

# Answer question
curl -X POST "https://betterask.dev/session/answer" \
  -H "Content-Type: application/json" \
  -d '{"session_id": "your-session-id", "answer": "Your answer here"}'

# Get summary  
curl "https://betterask.dev/session/your-session-id/summary"
```

## Best Practices

### For Optimal Conversations
1. **Start with context** - Choose the right context for your use case
2. **Encourage detail** - Longer answers generally produce better insights
3. **Follow threads** - The AI will reference specific details from answers
4. **Complete sessions** - Full 7-question conversations produce the richest insights
5. **Use human_id** - For persistent learning across multiple conversations

### For Integration
1. **Handle rate limits** - Wait 5+ seconds between answers
2. **Implement retries** - Network issues can interrupt conversations
3. **Store session IDs** - Keep track of active conversations
4. **Plan for LLM failures** - Conversations can continue with corpus questions
5. **Monitor session limits** - Max 3 active per API key

### For Analysis
1. **Track depth scores** - Monitor engagement quality over time
2. **Analyze vector patterns** - See which question types work best  
3. **Review avoided topics** - Understand conversation gaps
4. **Use suggested followups** - Plan future conversation sessions

---

## Support

For questions about Conversation Mode:
- **Documentation**: https://docs.betterask.dev/conversation  
- **API Status**: https://status.betterask.dev
- **Support**: support@betterask.dev

Rate limits, pricing, and feature availability may vary by subscription tier.