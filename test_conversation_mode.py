"""
Test script for Conversation Mode functionality.
Run with: python test_conversation_mode.py
"""

import os
import sys
import json
from unittest.mock import Mock, patch
from datetime import datetime

# Mock the dependencies that may not be installed
sys.modules['stripe'] = Mock()
sys.modules['psycopg2'] = Mock()
sys.modules['psycopg2.extras'] = Mock()
sys.modules['httpx'] = Mock()

# Mock the database connection
mock_cursor = Mock()
mock_conn = Mock()
mock_conn.cursor.return_value = mock_cursor
mock_cursor.fetchone.return_value = None
mock_cursor.fetchall.return_value = []

with patch('main.get_db', return_value=mock_conn):
    from main import (
        SessionStartRequest, SessionAnswerRequest,
        create_conversation_session, get_conversation_session,
        get_conversation_progression_vectors, select_next_question_vectors,
        analyze_answer_with_llm
    )

def test_conversation_progression_vectors():
    """Test vector selection based on conversation progression."""
    print("Testing conversation progression vectors...")
    
    # Test warm start (questions 1-2)
    warm_vectors = get_conversation_progression_vectors(1, 7)
    assert "specificity" in warm_vectors or "permission" in warm_vectors
    print("✓ Warm start vectors correct")
    
    # Test deep dive (questions 3-5)
    deep_vectors = get_conversation_progression_vectors(4, 7) 
    assert any(v in deep_vectors for v in ["confession", "perspective_shift", "other_eyes"])
    print("✓ Deep dive vectors correct")
    
    # Test reflective close (questions 6-7)
    reflective_vectors = get_conversation_progression_vectors(6, 7)
    assert any(v in reflective_vectors for v in ["time", "trajectory", "cumulation"])
    print("✓ Reflective vectors correct")

def test_session_models():
    """Test Pydantic models for conversation mode."""
    print("Testing session models...")
    
    # Test SessionStartRequest
    start_req = SessionStartRequest(
        context="discovery",
        human_id="test_user",
        session_length=5
    )
    assert start_req.context == "discovery"
    assert start_req.session_length == 5
    print("✓ SessionStartRequest model valid")
    
    # Test SessionAnswerRequest
    answer_req = SessionAnswerRequest(
        session_id="test-uuid",
        answer="This is my answer to the question."
    )
    assert answer_req.session_id == "test-uuid"
    assert len(answer_req.answer) > 0
    print("✓ SessionAnswerRequest model valid")

def test_vector_selection_logic():
    """Test the intelligent vector selection for next questions."""
    print("Testing vector selection logic...")
    
    # Mock analysis with thread opportunities
    analysis_with_threads = {
        "thread_opportunities": ["explore their childhood"],
        "avoided": ["family relationships"],
        "themes_identified": ["independence", "control"]
    }
    
    used_vectors = ["specificity", "specificity", "permission"]  # specificity used twice
    
    next_vectors = select_next_question_vectors(3, 7, analysis_with_threads, used_vectors)
    
    # Should prefer exploration vectors and avoid overused ones
    assert "specificity" not in next_vectors  # Already used twice
    assert len(next_vectors) <= 3
    print("✓ Vector selection logic working")

def test_mock_llm_analysis():
    """Test the fallback analysis when LLM is not available."""
    print("Testing mock LLM analysis...")
    
    with patch('main.ANTHROPIC_API_KEY', ''), patch('main.GEMINI_API_KEY', ''):
        analysis = analyze_answer_with_llm(
            "I love hiking because it clears my head and gives me perspective.",
            "What's your favorite way to spend free time?",
            []
        )
    
    # Should return fallback analysis structure
    assert "revealed" in analysis
    assert "depth_score" in analysis
    assert isinstance(analysis["depth_score"], (int, float))
    print("✓ Fallback LLM analysis working")

def run_tests():
    """Run all conversation mode tests."""
    print("=== BetterAsk Conversation Mode Tests ===\n")
    
    try:
        test_conversation_progression_vectors()
        test_session_models() 
        test_vector_selection_logic()
        test_mock_llm_analysis()
        
        print("\n✅ All tests passed! Conversation Mode implementation looks good.")
        return True
        
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)