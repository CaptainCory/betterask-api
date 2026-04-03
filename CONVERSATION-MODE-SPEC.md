# BetterAsk Conversation Mode - Technical Specification

## Overview

This spec defines the "Conversation Mode" feature for BetterAsk — a multi-turn conversation system where each answer informs the next question, revealing deep insights about a person through structured dialogue.

## Current Architecture Analysis

### Existing Components
- **FastAPI Application** (`main.py`) with PostgreSQL backend
- **Core `/ask` endpoint** - stateless question engine with optional persistent profiles
- **607-question corpus** with 21 vectors (tagged_corpus.json)
- **Human profiles table** - tracks known data, questions asked, domains covered
- **Question generation** - LLM-powered via Claude Opus/Gemini with corpus fallback
- **Gap analysis** - identifies knowledge gaps and recommends questions

### Key Models & Tables
```sql
human_profiles (human_id, agent_api_key, known_data, domains_covered, questions_asked)
questions (corpus questions with vectors, archetypes, scoring)
question_performance (tracks effectiveness of questions)
```

## New Feature: Conversation Mode

### Core Concept
Transform the stateless `/ask` API into a structured conversation flow:
1. **Session Start** - Begin with warm, accessible questions
2. **Progressive Depth** - Each answer analyzed to select optimal next question
3. **Insight Synthesis** - Real-time analysis of what's revealed vs. avoided
4. **Session Summary** - Deep personality synthesis after completion

### New Endpoints

#### 1. POST /session/start

Creates a new conversation session with strategic question selection.

**Request:**
```json
{
  "context": "discovery",           // optional: context for question strategy
  "human_id": "user_123",          // optional: for persistence
  "session_length": 7,             // optional: default 7 questions
  "starting_vectors": ["specificity", "name_an_example"]  // optional: override default warm start
}
```

**Response:**
```json
{
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "question": {
    "question": "What's something you wish people understood about you without having to explain it?",
    "follow_up": "How do you usually handle it when they don't get it?",
    "vectors": ["specificity", "permission"],
    "vector_names": ["Specificity", "Permission to be Real"],
    "gap_targeted": "self_expression",
    "why": "Starting with accessible self-reflection to build rapport and establish baseline",
    "what_to_listen_for": "Identity markers, communication preferences, frustration patterns"
  },
  "question_number": 1,
  "total_planned": 7,
  "strategy": "warm_start"         // warm_start, mid_depth, deep_dive
}
```

**Logic:**
- Generate session_id (UUID)
- Create session record in new `conversation_sessions` table
- Use warm, accessible vectors (specificity, name_an_example, permission)
- Select first question using existing `/ask` logic but with conversation-optimized parameters

#### 2. POST /session/answer

Processes an answer and generates the next strategic question.

**Request:**
```json
{
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "answer": "I think people assume I'm really organized and have my life together because of my job, but actually I'm pretty scattered and just good at making things look polished on the outside."
}
```

**Response:**
```json
{
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "insight": {
    "revealed": [
      "Gap between public persona and private reality",
      "Professional identity creates expectations",
      "Skill at surface-level presentation",
      "Self-awareness about personal disorganization"
    ],
    "avoided": [
      "Specific examples of being scattered",
      "Emotional impact of maintaining facade"
    ],
    "contradictions": [],
    "depth_score": 6,
    "themes_identified": ["authentic_self", "professional_identity", "perfectionism"]
  },
  "next_question": {
    "question": "When did you first realize you were good at making chaos look organized?",
    "follow_up": "What's the most scattered thing about you that nobody would guess?",
    "vectors": ["time", "specificity", "confession"],
    "vector_names": ["Time", "Specificity", "Confession"],
    "gap_targeted": "authentic_self_vs_persona",
    "why": "Following the thread about persona management - asking for origin story and deeper confession",
    "what_to_listen_for": "Origin moments, specific examples of chaos, emotional relationship to this pattern"
  },
  "question_number": 2,
  "vectors_engaged": ["specificity", "permission", "time", "confession"],
  "vectors_untouched": ["hypothetical", "perspective_shift", "comparison", "subversion"],
  "conversation_depth": "building"
}
```

**Logic:**
1. **Answer Analysis** (LLM call):
   - Extract revealed information
   - Detect avoidance patterns  
   - Identify contradictions with prior answers
   - Score depth (0-10)
   - Update themes and insights

2. **Strategic Question Selection**:
   - Pull threads from specific phrases in the answer
   - Target gaps/avoidances from insight analysis
   - Progress vector strategy (warm → deep → reflective)
   - Use existing question generation but with conversation context

3. **Update Session State**:
   - Store answer and insights
   - Update vector tracking
   - Increment question counter

#### 3. GET /session/{session_id}/summary

Returns comprehensive session insights after completion (or mid-session).

**Response:**
```json
{
  "session_id": "550e8400-e29b-41d4-a716-446655440000",
  "session_status": "complete",     // complete, in_progress, abandoned
  "questions_answered": 7,
  "duration_minutes": 23,
  "structural_insights": [
    "Strong pattern of maintaining polished exterior while acknowledging internal chaos",
    "Identifies as someone who succeeds despite disorganization, not because of organization", 
    "Values authenticity but struggles with vulnerability in professional contexts"
  ],
  "ephemeral_insights": [
    "Currently stressed about upcoming project deadline",
    "Recently moved to new city and adjusting to change"
  ],
  "personality_sketch": "This person presents a fascinating paradox: professionally competent yet personally scattered, successful at projecting organization while privately embracing chaos. They possess strong self-awareness about this duality and seem to find both humor and frustration in maintaining their polished exterior. There's a deep value for authenticity coupled with practical necessities that keep them somewhat guarded. They're likely creative, adaptable, and more resilient than they give themselves credit for.",
  "vectors_engaged": {
    "specificity": 8.5,
    "confession": 7.2,
    "time": 6.8,
    "permission": 6.1,
    "perspective_shift": 5.3
  },
  "vectors_avoided": {
    "hypothetical": 2.1,
    "comparison": 1.8,
    "subversion": 1.5
  },
  "suggested_followup": [
    "What would change if you let people see the scattered version of you more often?",
    "When you do let your guard down, what's the reaction you get?",
    "What's the most authentic thing about how you work that contradicts your professional image?"
  ],
  "conversation_quality": {
    "engagement_score": 8.2,
    "depth_achieved": 7.5,
    "breakthrough_moments": 2,
    "avoidance_instances": 3
  }
}
```

### Database Schema Changes

#### New Table: conversation_sessions
```sql
CREATE TABLE conversation_sessions (
    session_id TEXT PRIMARY KEY,
    human_id TEXT,
    api_key TEXT,
    status TEXT DEFAULT 'active',          -- active, complete, abandoned
    total_planned INTEGER DEFAULT 7,
    questions_answered INTEGER DEFAULT 0,
    context TEXT DEFAULT 'discovery',
    strategy TEXT DEFAULT 'progressive',   -- progressive, targeted, exploratory
    started_at TEXT DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT,
    expires_at TEXT,                       -- 24 hours from creation
    
    -- Session state (JSON columns)
    conversation_data TEXT DEFAULT '{}',   -- all Q&As, insights, themes
    vector_progress TEXT DEFAULT '{}',     -- which vectors used, scores
    insights_cumulative TEXT DEFAULT '{}', -- running insights analysis
    
    FOREIGN KEY (api_key) REFERENCES api_keys(key)
);
CREATE INDEX idx_sessions_human_id ON conversation_sessions(human_id);
CREATE INDEX idx_sessions_status ON conversation_sessions(status);
CREATE INDEX idx_sessions_expires ON conversation_sessions(expires_at);
```

#### New Table: conversation_turns
```sql
CREATE TABLE conversation_turns (
    id SERIAL PRIMARY KEY,
    session_id TEXT NOT NULL,
    turn_number INTEGER NOT NULL,
    question_text TEXT NOT NULL,
    question_vectors TEXT DEFAULT '[]',
    answer_text TEXT,
    answer_analysis TEXT DEFAULT '{}',     -- JSON: revealed, avoided, depth_score
    gap_targeted TEXT,
    insights_generated TEXT DEFAULT '[]',
    answered_at TEXT,
    
    FOREIGN KEY (session_id) REFERENCES conversation_sessions(session_id) ON DELETE CASCADE
);
CREATE INDEX idx_turns_session ON conversation_turns(session_id);
CREATE INDEX idx_turns_answered ON conversation_turns(session_id, answered_at);
```

### Implementation Plan

#### Phase 1: Core Infrastructure
1. **Database setup** - Add new tables with proper indexes
2. **Session management** - Create, retrieve, expire sessions (24h TTL)
3. **Basic conversation flow** - Start session, accept answers, track state
4. **Session cleanup** - Background task to clean expired sessions

#### Phase 2: Intelligence Layer
1. **Answer analysis LLM integration** - Parse answers for insights
2. **Strategic question selection** - Progress through conversation stages
3. **Vector progression** - Warm start → deep dive → reflective close
4. **Thread pulling** - Generate questions that follow up on specific answer details

#### Phase 3: Insights & Analytics
1. **Real-time insight generation** - Track revelations and avoidances
2. **Session summaries** - Comprehensive analysis at completion
3. **Performance tracking** - Which questions/vectors generate best insights
4. **Followup recommendations** - Suggest questions for future sessions

### Question Selection Strategy

#### Progression Model
```
Questions 1-2: Warm Start (vectors: specificity, name_an_example, permission)
Questions 3-5: Deep Dive (vectors: confession, perspective_shift, contradiction, other_eyes)
Questions 6-7: Reflective (vectors: time, trajectory, cumulation)
```

#### Thread Pulling Algorithm
1. **Parse answer for key phrases** - Extract specific details, emotional markers
2. **Identify gaps** - What topics were touched but not explored?
3. **Detect avoidance** - What questions were deflected or answered superficially?
4. **Generate follow-up** - Create questions that pursue the most promising threads

### Rate Limiting & Security

- **Rate limiting**: 1 answer per 5 seconds per session (prevent spam)
- **Session limits**: 3 active sessions per API key maximum
- **Cleanup**: Auto-expire sessions after 24 hours
- **API key required** for session creation (no anonymous conversations)

### LLM Integration

#### Answer Analysis Prompt Template
```
You are analyzing a conversation answer to generate insights and guide the next question.

ANSWER: "{answer}"

CONVERSATION CONTEXT:
- Question asked: "{previous_question}"  
- Prior insights: {cumulative_insights}
- Vectors already explored: {vectors_used}

ANALYZE:
1. REVEALED: What specific things did this answer reveal about the person?
2. AVOIDED: What topics/details did they seem to skip or deflect?
3. CONTRADICTIONS: Does this conflict with anything they said before?
4. DEPTH_SCORE: Rate 0-10 how deeply they engaged with the question
5. THEMES: What life themes/patterns are emerging?

OUTPUT (JSON):
{
  "revealed": ["specific insight 1", "insight 2"],
  "avoided": ["avoided topic 1", "avoided topic 2"],
  "contradictions": ["contradiction if any"],
  "depth_score": 7,
  "themes": ["theme_1", "theme_2"],
  "emotional_markers": ["marker_1"],
  "thread_opportunities": ["follow_up_angle_1", "angle_2"]
}
```

#### Next Question Generation
- Use existing `generate_question_via_llm()` function
- Inject conversation context and thread opportunities
- Bias vector selection based on conversation stage
- Follow thread opportunities from answer analysis

### Testing Strategy

#### Unit Tests
- Session CRUD operations
- Answer analysis parsing
- Vector progression logic
- Rate limiting enforcement

#### Integration Tests
- Full conversation flow (start → answer → answer → complete)
- Session expiration handling
- Error handling for malformed inputs
- LLM fallback behavior

#### Load Tests
- Concurrent session handling
- Database performance with large session datasets
- Memory usage for long conversations

### Monitoring & Analytics

#### Session Metrics
- Completion rate (% of started sessions that reach 7 questions)
- Average session duration
- Depth scores by question number
- Most engaging vectors by position

#### Question Performance  
- Which generated questions get the deepest responses?
- Vector combinations that produce best insights
- Common avoidance patterns by topic

### Migration Plan

#### Deployment
1. **Schema migration** - Add new tables without affecting existing functionality  
2. **Feature flag** - Deploy code but keep conversation endpoints disabled
3. **Testing** - Validate with internal team using test API keys
4. **Gradual rollout** - Enable for Pro/Scale tiers first
5. **Monitor** - Watch for performance impacts on existing `/ask` endpoint

#### Backward Compatibility
- All existing endpoints remain unchanged
- New conversation endpoints are additive
- Existing human_profiles table continues to work with `/ask`

### Success Metrics

#### Product Metrics
- **Engagement**: Average questions answered per session (target: 5+/7)
- **Depth**: Average depth score progression (should increase through conversation)
- **Completion**: Session completion rate (target: 60%+)
- **Insights**: Quality of generated personality sketches (qualitative review)

#### Technical Metrics  
- **Performance**: Session start latency (<500ms)
- **Reliability**: Error rate for answer processing (<2%)
- **Scalability**: Support 1000+ concurrent sessions
- **Cost**: LLM API costs per completed session

## Implementation Notes

### Edge Cases
- **Session timeout** - Handle gracefully with partial summary
- **Empty answers** - Re-prompt or skip with insight noting avoidance
- **Very long answers** - Truncate but preserve analysis quality
- **LLM failures** - Fallback to corpus questions with basic insights

### Security Considerations
- **PII handling** - Don't log sensitive personal details from answers
- **Session isolation** - Ensure sessions can't access each other's data
- **Rate limiting** - Prevent abuse of expensive LLM calls
- **Data retention** - Clear conversation data based on user tier policies

### Performance Optimizations
- **Answer analysis caching** - Cache insights for identical answers
- **Session state compression** - JSON compress large conversation_data
- **Database indexing** - Optimize for session lookup patterns
- **LLM batching** - Group analysis calls when possible

## Recallability Scoring

Every generated question is scored for recallability (0-10) before being presented.

### Principle
"The best questions are easy to answer but hard to answer shallowly."

### Scoring Factors
| Factor | Effect | Example |
|--------|--------|---------|
| Exact counts | -4.0 | "How many times have you..." |
| Percentages | -3.5 | "What percentage of your day..." |
| Ranked lists beyond #1 | -3.0 | "What's the third most..." |
| Aggregated stats | -2.5 | "On average, how many..." |
| Specific dates | -2.0 | "When exactly did you first..." |
| Opinions/feelings | +1.5 | "What do you think about..." |
| Current state | +1.0 | "Right now, what..." |
| Identity questions | +1.5 | "Are you someone who..." |
| Softeners | +1.5 | "Roughly how..." |

### Thresholds
- Score < 4.0: Attempt regeneration or corpus fallback
- Score 4.0-7.0: Acceptable
- Score > 7.0: Ideal

---

This spec provides the foundation for transforming BetterAsk from a stateless question API into a conversation product that reveals deep insights about people through strategic, multi-turn dialogue.