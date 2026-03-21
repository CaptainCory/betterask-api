#!/usr/bin/env python3
"""
Quick test script to verify the privacy endpoints work correctly.
"""

import json
import requests
from datetime import datetime

# Configuration
BASE_URL = "http://localhost:8000"
API_KEY = "ba_demo_public_readonly"  # Use demo key for testing

def test_privacy_endpoints():
    """Test the new privacy endpoints."""
    
    print("🔐 Testing BetterAsk Privacy & Data Management Endpoints")
    print("="*60)
    
    # Test 1: Privacy Audit (should return 404 for non-existent profile)
    print("\n1️⃣ Testing Privacy Audit endpoint...")
    response = requests.get(
        f"{BASE_URL}/privacy/test_user_123",
        headers={"X-API-Key": API_KEY}
    )
    
    if response.status_code == 404:
        print("✅ Privacy audit correctly returns 404 for non-existent profile")
    else:
        print(f"❌ Expected 404, got {response.status_code}: {response.text}")
    
    # Test 2: Export Profile (should return 404 for non-existent profile)
    print("\n2️⃣ Testing Profile Export endpoint...")
    response = requests.post(
        f"{BASE_URL}/profile/test_user_123/export",
        headers={"X-API-Key": API_KEY}
    )
    
    if response.status_code == 404:
        print("✅ Profile export correctly returns 404 for non-existent profile")
    else:
        print(f"❌ Expected 404, got {response.status_code}: {response.text}")
    
    # Test 3: Delete Profile (should return 404 for non-existent profile)
    print("\n3️⃣ Testing Profile Deletion endpoint...")
    response = requests.delete(
        f"{BASE_URL}/profile/test_user_123",
        headers={"X-API-Key": API_KEY}
    )
    
    if response.status_code == 404:
        print("✅ Profile deletion correctly returns 404 for non-existent profile")
    else:
        print(f"❌ Expected 404, got {response.status_code}: {response.text}")
    
    # Test 4: Check privacy headers on /ask endpoint
    print("\n4️⃣ Testing Privacy Headers on /ask endpoint...")
    response = requests.post(
        f"{BASE_URL}/ask",
        headers={"X-API-Key": API_KEY},
        json={
            "memory": "Test user for privacy endpoint testing",
            "agent_role": "personal assistant",
            "count": 1,
            "human_id": "test_privacy_user"  # This should trigger X-Data-Stored: true
        }
    )
    
    if response.status_code == 200:
        headers = response.headers
        if "X-Privacy-Policy" in headers:
            print(f"✅ Privacy policy header present: {headers['X-Privacy-Policy']}")
        else:
            print("❌ Privacy policy header missing")
            
        if "X-Data-Stored" in headers:
            print(f"✅ Data stored header present: {headers['X-Data-Stored']}")
            if headers["X-Data-Stored"] == "true":
                print("✅ Correctly indicates data was stored (human_id provided)")
            else:
                print("❌ Should indicate data was stored")
        else:
            print("❌ Data stored header missing")
    else:
        print(f"❌ /ask endpoint error: {response.status_code}: {response.text}")
    
    # Test 5: Check privacy headers on /ask endpoint without human_id
    print("\n5️⃣ Testing Privacy Headers on /ask endpoint (no human_id)...")
    response = requests.post(
        f"{BASE_URL}/ask",
        headers={"X-API-Key": API_KEY},
        json={
            "memory": "Test user for privacy endpoint testing",
            "agent_role": "personal assistant",
            "count": 1
            # No human_id - should trigger X-Data-Stored: false
        }
    )
    
    if response.status_code == 200:
        headers = response.headers
        if "X-Data-Stored" in headers:
            print(f"✅ Data stored header present: {headers['X-Data-Stored']}")
            if headers["X-Data-Stored"] == "false":
                print("✅ Correctly indicates no data was stored (no human_id)")
            else:
                print("❌ Should indicate no data was stored")
        else:
            print("❌ Data stored header missing")
    else:
        print(f"❌ /ask endpoint error: {response.status_code}: {response.text}")
    
    print("\n🎉 Privacy endpoint testing complete!")
    print("\nTo test with real data:")
    print("1. Create a profile with /ask using human_id")
    print("2. Use /learn to add data to the profile") 
    print("3. Test /privacy/{human_id} to see data audit")
    print("4. Test /profile/{human_id}/export to get full export")
    print("5. Test DELETE /profile/{human_id} to delete all data")

if __name__ == "__main__":
    try:
        test_privacy_endpoints()
    except requests.exceptions.ConnectionError:
        print("❌ Could not connect to BetterAsk API at http://localhost:8000")
        print("Make sure the server is running with: uvicorn main:app --reload")
    except Exception as e:
        print(f"❌ Test failed with error: {e}")