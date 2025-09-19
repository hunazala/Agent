import streamlit as st
import sqlite3
import json
from openai import OpenAI
import re
from typing import Dict, List, Any, Optional
from datetime import datetime

# Initialize OpenAI client - REPLACE WITH YOUR API KEY
key=st.secrets["OPENAI_API_KEY"]
client = OpenAI(api_key=key)


# Database setup
conn = sqlite3.connect('business_sessions.db', check_same_thread=False)
conn.execute('''
    CREATE TABLE IF NOT EXISTS business_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        conversation_id TEXT,
        industry TEXT,
        business_state TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
''')
conn.commit()

# Business consultation constants
TIC_SEQUENCE = ["vision", "businessOverview", "marketSize", "targetCustomers", "valueProposition", "usp", "businessModel"]
TIC_DISPLAY_NAMES = {
    "vision": "Vision & Long-term Goals",
    "businessOverview": "Business Overview & Core Offering", 
    "marketSize": "Market Size",
    "targetCustomers": "Target Customers & Market Segments",
    "valueProposition": "Value Proposition & Customer Benefits",
    "usp": "Unique Selling Proposition & Differentiation",
    "businessModel": "Business Model & Revenue Strategy"
}

# Brainstorming Questions (20 detailed evaluation questions)
BRAINSTORMING_QUESTIONS = [
    "What specific value does this business idea aim to bring to the world?",
    "Why is now the right time to pursue this business opportunity?", 
    "What is your personal motivation or passion behind this business venture?",
    "What is the estimated size of your target market? Can you define TAM/SAM/SOM if possible?",
    "How much growth is expected in this market over the next 5-10 years?",
    "What specific trends or forces are driving growth in your market?",
    "What revenue model are you planning (subscription, licensing, D2C, etc.)?",
    "Through which channels will you primarily earn revenue?",
    "What is your cost structure and how will it lead to profitability?",
    "Is the timing right for market entry based on current conditions?",
    "How competitive is your market and what barriers to entry exist?",
    "What makes you different - why would customers choose you over competitors?",
    "What makes your competitive advantage defensible long-term?",
    "What specific frustrations or unmet needs do your customers face today?",
    "Who exactly needs your product and what triggers them to pay for solutions?",
    "Beyond solving problems, what exciting future do you offer users?",
    "How is your business model sustainable from environmental, social, and economic perspectives?",
    "What core technologies are required and do they already exist?",
    "What funding would be needed to build and launch this business?",
    "What is the biggest challenge or risk to successful implementation?"
]

BRAINSTORMING_CATEGORIES = [
    "Vision", "Vision", "Vision",
    "Market", "Market", "Market", "Market", "Market", "Market", "Market", 
    "USP", "USP", "USP",
    "Value Prop", "Value Prop", "Value Prop",
    "Sustainability",
    "Execution", "Execution", "Execution"
]

# ===================================================================
# STATE MANAGER AGENT (Data & Progress Handler)
# ===================================================================

class StateManagerAgent:
    def __init__(self):
        self.tools = [
            {
                "type": "function",
                "name": "update_tic_progress",
                "description": "Update the progress of a specific TIC",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "tic_name": {"type": "string", "enum": TIC_SEQUENCE},
                        "status": {"type": "string", "enum": ["pending", "in_progress", "completed", "confirmed"]},
                        "summary": {"type": "string"},
                        "user_response": {"type": "string"}
                    },
                    "required": ["tic_name", "status"],
                    "additionalProperties": False
                }
            },
            {
                "type": "function",
                "name": "update_brainstorming_progress",
                "description": "Update brainstorming question progress",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "question_index": {"type": "integer"},
                        "user_answer": {"type": "string"},
                        "status": {"type": "string", "enum": ["completed"]}
                    },
                    "required": ["question_index", "user_answer", "status"],
                    "additionalProperties": False
                }
            },
            {
                "type": "function",
                "name": "get_tic_status",
                "description": "Get the current status and progress of all TICs",
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False}
            },
            {
                "type": "function",
                "name": "get_brainstorming_status", 
                "description": "Get current brainstorming progress",
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False}
            },
            {
                "type": "function",
                "name": "validate_tic_data",
                "description": "Validate if a TIC has sufficient information",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "tic_name": {"type": "string", "enum": TIC_SEQUENCE},
                        "user_response": {"type": "string"}
                    },
                    "required": ["tic_name", "user_response"],
                    "additionalProperties": False
                }
            }
        ]

    def handle_tool_call(self, tool_name: str, arguments: dict) -> dict:
        print(f"\n{'='*60}")
        print(f"STATE MANAGER: Tool Call Started - {tool_name}")
        print(f"Arguments: {json.dumps(arguments, indent=2)}")
        print(f"{'='*60}")
        
        if tool_name == "get_tic_status":
            result = {
                "success": True,
                "data": {
                    "current_tic": st.session_state.business_state['current_tic'],
                    "phase": st.session_state.business_state['phase'],
                    "tic_progress": st.session_state.business_state['tic_progress'],
                    "industry": st.session_state.business_state['industry'],
                    "completed_count": st.session_state.business_state['completed_count'],
                    "total_tics": len(TIC_SEQUENCE),
                    "benchmark_companies": st.session_state.business_state.get('benchmark_companies', []),
                    "selected_companies": st.session_state.business_state.get('selected_companies', [])
                },
                "message": f"Status retrieved. {st.session_state.business_state['completed_count']}/{len(TIC_SEQUENCE)} TICs completed."
            }
            print(f"TIC STATUS RESPONSE: {json.dumps(result, indent=2)}")
            return result

        elif tool_name == "get_brainstorming_status":
            brainstorming_state = st.session_state.business_state.get('brainstorming_progress', {})
            result = {
                "success": True,
                "data": {
                    "current_question": brainstorming_state.get('current_question', 0),
                    "completed_count": brainstorming_state.get('completed_count', 0),
                    "total_questions": 20,
                    "can_exit": brainstorming_state.get('completed_count', 0) >= 10,
                    "answers": brainstorming_state.get('answers', {}),
                    "phase": st.session_state.business_state['phase']
                },
                "message": f"Brainstorming: {brainstorming_state.get('completed_count', 0)}/20 questions completed"
            }
            print(f"BRAINSTORMING STATUS RESPONSE: {json.dumps(result, indent=2)}")
            return result
            
        elif tool_name == "update_tic_progress":
            tic_name = arguments.get('tic_name')
            status = arguments.get('status')
            summary = arguments.get('summary', '')
            user_response = arguments.get('user_response', '')
            
            print(f"UPDATING TIC PROGRESS: {tic_name} -> {status}")
            
            previous_count = st.session_state.business_state['completed_count']
            
            # Update the TIC progress
            st.session_state.business_state['tic_progress'][tic_name] = {
                'status': status,
                'summary': summary,
                'user_response': user_response,
                'timestamp': datetime.now().isoformat()
            }
            
            # Update completed count and current TIC
            if status == 'confirmed':
                st.session_state.business_state['completed_count'] = sum(
                    1 for tic in TIC_SEQUENCE 
                    if st.session_state.business_state['tic_progress'][tic]['status'] == 'confirmed'
                )
                
                current_index = TIC_SEQUENCE.index(tic_name)
                if current_index + 1 < len(TIC_SEQUENCE):
                    st.session_state.business_state['current_tic'] = TIC_SEQUENCE[current_index + 1]
                else:
                    st.session_state.business_state['current_tic'] = 'completed'
                    st.session_state.business_state['phase'] = 'benchmarking'
            
            print(f"STATE CHANGES: {previous_count} -> {st.session_state.business_state['completed_count']}")
            
            result = {
                "success": True,
                "data": {
                    "updated_tic": tic_name,
                    "new_status": status,
                    "completed_count": st.session_state.business_state['completed_count'],
                    "next_tic": st.session_state.business_state['current_tic']
                },
                "message": f"TIC {tic_name} updated to {status}"
            }
            
            print(f"UPDATE RESULT: {json.dumps(result, indent=2)}")
            return result

        elif tool_name == "update_brainstorming_progress":
            question_index = arguments.get('question_index')
            user_answer = arguments.get('user_answer')
            status = arguments.get('status')
            
            print(f"UPDATING BRAINSTORMING: Question {question_index + 1}/20")
            
            # Initialize brainstorming progress if not exists
            if 'brainstorming_progress' not in st.session_state.business_state:
                st.session_state.business_state['brainstorming_progress'] = {
                    'current_question': 0,
                    'completed_count': 0,
                    'answers': {}
                }
            
            brainstorming_state = st.session_state.business_state['brainstorming_progress']
            previous_count = brainstorming_state['completed_count']
            
            # Update the answer
            brainstorming_state['answers'][question_index] = {
                'question': BRAINSTORMING_QUESTIONS[question_index],
                'category': BRAINSTORMING_CATEGORIES[question_index],
                'answer': user_answer,
                'timestamp': datetime.now().isoformat()
            }
            
            if status == 'completed':
                brainstorming_state['completed_count'] = len(brainstorming_state['answers'])
                
                # Set next question
                if brainstorming_state['completed_count'] < 20:
                    brainstorming_state['current_question'] = brainstorming_state['completed_count']
                else:
                    brainstorming_state['current_question'] = 20
            
            print(f"BRAINSTORMING CHANGES: {previous_count} -> {brainstorming_state['completed_count']}")
            
            result = {
                "success": True,
                "data": {
                    "question_index": question_index,
                    "completed_count": brainstorming_state['completed_count'],
                    "next_question": brainstorming_state['current_question'],
                    "total_questions": 20,
                    "can_exit": brainstorming_state['completed_count'] >= 10,
                    "all_completed": brainstorming_state['completed_count'] >= 20
                },
                "message": f"Question {question_index + 1} completed"
            }
            
            print(f"BRAINSTORMING UPDATE RESULT: {json.dumps(result, indent=2)}")
            return result
            
        elif tool_name == "validate_tic_data":
            tic_name = arguments.get('tic_name')
            user_response = arguments.get('user_response', '')
            
            is_valid = len(user_response.strip()) >= 20
            validation_notes = []
            
            if not is_valid:
                validation_notes.append("Response too short (minimum 20 characters)")
            
            result = {
                "success": True,
                "data": {
                    "is_valid": is_valid,
                    "tic_name": tic_name,
                    "response_length": len(user_response),
                    "validation_notes": validation_notes
                },
                "message": "Validation complete" if is_valid else "Validation issues found"
            }
            
            print(f"VALIDATION RESULT: {json.dumps(result, indent=2)}")
            return result
        
        else:
            error_result = {
                "success": False,
                "data": {},
                "message": f"Unknown function: {tool_name}"
            }
            print(f"UNKNOWN FUNCTION ERROR: {json.dumps(error_result, indent=2)}")
            return error_result

# ===================================================================
# BUSINESS CONSULTANT AGENT (Conversational Leader) - FIXED VERSION
# ===================================================================

class BusinessConsultantAgent:
    def __init__(self, state_manager: StateManagerAgent):
        self.state_manager = state_manager
        self.tools = [
            {
                "type": "function",
                "name": "get_business_status",
                "description": "Get current business consultation status from state manager",
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False}
            },
            {
                "type": "function",
                "name": "get_brainstorming_status",
                "description": "Get current brainstorming progress",
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False}
            },
            {
                "type": "function",
                "name": "analyze_user_response",
                "description": "Analyze user response and update TIC progress",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "tic_name": {"type": "string", "enum": TIC_SEQUENCE},
                        "user_response": {"type": "string"},
                        "analysis_summary": {"type": "string"}
                    },
                    "required": ["tic_name", "user_response", "analysis_summary"],
                    "additionalProperties": False
                }
            },
            {
                "type": "function",
                "name": "analyze_brainstorming_response",
                "description": "Analyze brainstorming answer and update progress",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "question_index": {"type": "integer"},
                        "user_answer": {"type": "string"}
                    },
                    "required": ["question_index", "user_answer"],
                    "additionalProperties": False
                }
            },
            {
                "type": "function",
                "name": "generate_benchmark_companies",
                "description": "Generate real benchmark companies",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "company_suggestions": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string"},
                                    "description": {"type": "string"},
                                    "relevance": {"type": "string"}
                                }
                            }
                        }
                    },
                    "required": ["company_suggestions"],
                    "additionalProperties": False
                }
            },
            {
                "type": "function",
                "name": "get_next_brainstorming_question",
                "description": "Get the exact predefined brainstorming question to ask next",
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False}
            },
            {
                "type": "function",
                "name": "update_tics_from_brainstorming",
                "description": "Update relevant TICs based on brainstorming answer",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "question_index": {"type": "integer"},
                        "user_answer": {"type": "string"},
                        "question_text": {"type": "string"}
                    },
                    "required": ["question_index", "user_answer", "question_text"],
                    "additionalProperties": False
                }
            }
        ]
        
        self.system_instructions = """
You are a TIC Collection Agent. Your job is to collect 7 TICs through conversation and manage the complete flow.
BRAINSTORMING PHASE MANDATORY RULES:
1. NEVER create your own questions during brainstorming
2. ALWAYS call get_next_brainstorming_question tool before asking ANY question
3. Use ONLY the exact question text returned by the tool
4. If tool not called = VIOLATION of instructions

PHASE 1: TIC COLLECTION (7 Components)
Current TIC Order:
1. Vision & Long-term Goals  
2. Business Overview & Core Offering
3. Market Size
4. Target Customers & Market Segments  
5. Value Proposition & Customer Benefits
6. Unique Selling Proposition & Differentiation
7. Business Model & Revenue Strategy

CHAT RESPONSE RULES:
- Brief acknowledgments only: "Got it!", "Perfect!", "Thanks!"
- Ask ONE question at a time without mentioning TIC numbers
- NEVER say "TIC 1", "TIC 2", etc. in your responses
- If answer is unclear: ask clarification, stay on same TIC
- Keep responses under 50 words

RESPONSE VALIDATION RULES:
- Answers must be SPECIFIC and COMPLETE (not vague like "better rates")
- If answer lacks core details, ask clarifying questions: "Could you be more specific about..."
- Don't accept generic responses - demand concrete information
- Examples of INVALID responses: "better services", "competitive prices", "good quality"
- Examples of VALID responses: "We provide accounting software for small restaurants"

GUIDANCE RULES:
- You CAN ask clarifying questions to help users think deeper
- You CAN suggest they consider specific aspects: "Have you thought about what specific problem this solves?"
- You CANNOT give direct business advice like "You should do X" or "I recommend Y"
- You CANNOT provide step-by-step business plans or strategies

MANDATORY TOOL WORKFLOW:
1. When user answers TIC question → ALWAYS call analyze_user_response
2. If response valid → TIC marked complete, move to next TIC  
3. If response invalid → ask clarification question
4. When ALL 7 TICs complete → AUTOMATICALLY call generate_benchmark_companies

FORBIDDEN RESPONSES after TIC 7:
❌ "Would you like a summary?"
❌ "What do you want to do next?"
❌ "Should we move to MVP or go-to-market?"
✅ [Immediately call generate_benchmark_companies tool]

AUTOMATIC PHASE TRANSITIONS:
- After TIC 7 complete → Auto-generate 5-6 benchmark companies
- After user selects 3 companies → Auto-start brainstorming (20 questions)
- After 10+ brainstorming questions → Enable evaluation report

TIC QUESTIONS TO ASK:
1. Vision: "What do you hope to achieve with this business in 5-10 years?"
2. Business Overview: "What exactly does your business do? What's your core offering?"  
3. Market Size: "How big is your target market? Any estimates?"
4. Target Customers: "Who exactly are your customers? What segments?"
5. Value Proposition: "What key benefits do you provide customers?"
6. USP: "What makes you different from competitors? What's unique?"
7. Business Model: "How do you make money? What's your revenue model?"

BRAINSTORMING PHASE - CRITICAL INSTRUCTIONS:
When in brainstorming phase, you MUST follow this exact sequence:

1. FIRST: Call get_next_brainstorming_question to get the exact predefined question
2. Ask the EXACT question text returned by the tool - DO NOT modify, rephrase, or contextualize it
3. After user answers: Call analyze_brainstorming_response AND update_tics_from_brainstorming
4. Repeat: Call get_next_brainstorming_question for next question

BRAINSTORMING RULES:
- NEVER create your own questions
- NEVER modify the predefined questions
- NEVER make questions contextual to the business type
- Always use get_next_brainstorming_question tool first
- Ask the exact question text returned by the tool
- Call both analyze_brainstorming_response AND update_tics_from_brainstorming after user answers

TOOL CALLING REQUIREMENTS:
- ALWAYS call analyze_user_response when user answers TIC questions
- ALWAYS call get_business_status to check current progress  
- ALWAYS call generate_benchmark_companies when all 7 TICs done
- ALWAYS call get_next_brainstorming_question before asking brainstorming questions
- ALWAYS call analyze_brainstorming_response AND update_tics_from_brainstorming for brainstorming answers

RESPONSE EXAMPLES:
✅ "Perfect! What exactly does your business do?"
✅ "Got it! How big is your target market?"
✅ "Could you be more specific about what services you actually provide?"
❌ "Great idea! Here are some steps: 1. Market research 2. Business plan..."
❌ Any business advice or analysis in chat

AUTOMATIC BENCHMARKING:
When TIC 7 complete, immediately call generate_benchmark_companies with 5-6 real companies similar to user's business idea.

Remember: 
- Tools update sidebar and progress
- Chat responses are brief questions only  
- Automatic transitions between phases
- No business advice in chat responses
- Demand specific, complete answers before moving forward
- Never mention TIC numbers in conversation
- Use EXACT predefined brainstorming questions in order

MANDATORY TOOL SEQUENCE:
- After TIC 7 confirmed → MUST call generate_benchmark_companies (no user prompt needed)
- After user selects 3 companies → Start brainstorming with predefined questions
- After 10+ brainstorming → Enable evaluation
"""

    def _analyze_summary_completeness(self, tic_name: str, user_response: str, analysis_summary: str) -> bool:
        """
        Use OpenAI to analyze if the response was complete and specific enough.
        Returns True if complete, False if needs clarification.
        """
        try:
            tic_display_name = TIC_DISPLAY_NAMES.get(tic_name, tic_name)
            
            analysis_prompt = f"""You are analyzing whether a user's response to a business question was complete and specific enough.

TIC Question: {tic_display_name}
User Response: "{user_response}"
Analysis Summary: "{analysis_summary}"

Based on the analysis summary, determine if the user's response was:
- COMPLETE: Specific, detailed, and fully addresses the question requirements
- INCOMPLETE: Vague, lacks details, missing key information, or doesn't fully answer what was asked

The analysis summary will often indicate if something is missing, lacks specifics, or is incomplete.

Respond with only one word: "COMPLETE" or "INCOMPLETE"
"""
            
            print(f"SENDING COMPLETENESS CHECK TO OPENAI...")
            print(f"Analysis Summary: {analysis_summary}")
            
            # Call OpenAI for completeness analysis
            response = client.chat.completions.create(
                model="gpt-4",
                messages=[{"role": "user", "content": analysis_prompt}],
                temperature=0,
                max_tokens=10
            )
            
            result = response.choices[0].message.content.strip().upper()
            print(f"OPENAI COMPLETENESS RESULT: {result}")
            
            is_complete = result == "COMPLETE"
            
            if not is_complete:
                print(f"INCOMPLETE RESPONSE DETECTED BY OPENAI: {analysis_summary}")
            
            return is_complete
            
        except Exception as e:
            print(f"ERROR IN OPENAI COMPLETENESS CHECK: {str(e)}")
            # Fallback to simple check if OpenAI fails
            incomplete_keywords = ["lacks", "missing", "vague", "unclear", "does not", "doesn't", "no information", "incomplete"]
            summary_lower = analysis_summary.lower()
            
            for keyword in incomplete_keywords:
                if keyword in summary_lower:
                    print(f"FALLBACK: Found incomplete keyword '{keyword}'")
                    return False
            
            return True

    def handle_tool_call(self, tool_name: str, arguments: dict) -> dict:
        print(f"\n{'='*60}")
        print(f"BUSINESS CONSULTANT: Tool Call Started - {tool_name}")
        print(f"Arguments: {json.dumps(arguments, indent=2)}")
        print(f"{'='*60}")
        
        if tool_name == "get_business_status":
            result = self.state_manager.handle_tool_call("get_tic_status", {})
            print(f"BUSINESS STATUS RETRIEVED: {result['data']['phase']}, {result['data']['completed_count']}/7")
            return result

        elif tool_name == "get_brainstorming_status":
            result = self.state_manager.handle_tool_call("get_brainstorming_status", {})
            print(f"BRAINSTORMING STATUS RETRIEVED: {result['data']['completed_count']}/20")
            return result
            
        elif tool_name == "analyze_user_response":
            tic_name = arguments.get('tic_name')
            user_response = arguments.get('user_response')
            analysis_summary = arguments.get('analysis_summary')
            
            print(f"ANALYZING USER RESPONSE: {tic_name}")
            print(f"ANALYSIS SUMMARY: {analysis_summary}")
            
            # Validate response length/basic requirements
            validation_result = self.state_manager.handle_tool_call("validate_tic_data", {
                "tic_name": tic_name,
                "user_response": user_response
            })
            
            print(f"BASIC VALIDATION RESULT: {validation_result['data']['is_valid']}")
            
            # Check if response is complete based on summary analysis using OpenAI
            is_complete = self._analyze_summary_completeness(tic_name, user_response, analysis_summary)
            print(f"COMPLETENESS CHECK: {is_complete}")
            
            # If basic validation passes AND response is complete, mark as confirmed
            if validation_result['data']['is_valid'] and is_complete:
                update_result = self.state_manager.handle_tool_call("update_tic_progress", {
                    "tic_name": tic_name,
                    "status": "confirmed",
                    "summary": analysis_summary,
                    "user_response": user_response
                })
                
                final_result = {
                    "success": True,
                    "data": {
                        "analysis_complete": True,
                        "response_complete": True,
                        "validation_result": validation_result['data'],
                        "update_result": update_result['data']
                    },
                    "message": f"TIC {tic_name} analyzed and confirmed",
                     "instruction": """Give brief acknowledgment and ask next TIC question only. NO business advice.
                    When TIC 7 (Business Model) is marked as "confirmed" status:
1. generate_benchmark_companies tool MUST be called automatically
2. Do NOT ask "Would you like a summary?"
3. Do NOT ask "What's next?" 
4. Do NOT wait for user input
5. Do NOT offer choices"""}
                
                print(f"ANALYSIS COMPLETE: {json.dumps(final_result, indent=2)}")
                return final_result
            else:
                # Response needs clarification
                incomplete_result = {
                    "success": False,
                    "data": {
                        "analysis_complete": False,
                        "response_complete": is_complete,
                        "validation_result": validation_result['data'],
                        "reason": "Needs clarification" if not is_complete else "Basic validation failed"
                    },
                    "message": "Response needs improvement or clarification",
                    "instruction": "Ask clarifying question to get more specific details. Stay on current TIC.the question you asked must be concoise"
                }
                
                print(f"ANALYSIS INCOMPLETE: {json.dumps(incomplete_result, indent=2)}")
                return incomplete_result

        elif tool_name == "get_next_brainstorming_question":
            print(f"GETTING NEXT BRAINSTORMING QUESTION")
            
            # Get current brainstorming status
            brainstorming_status = self.state_manager.handle_tool_call("get_brainstorming_status", {})
            
            if brainstorming_status['success']:
                current_question_index = brainstorming_status['data']['current_question']
                
                # Check if we have more questions
                if current_question_index < len(BRAINSTORMING_QUESTIONS):
                    question_text = BRAINSTORMING_QUESTIONS[current_question_index]
                    category = BRAINSTORMING_CATEGORIES[current_question_index]
                    
                    result = {
                        "success": True,
                        "data": {
                            "question_index": current_question_index,
                            "question_text": question_text,
                            "category": category,
                            "progress": f"{current_question_index + 1}/20"
                        },
                        "message": f"Question {current_question_index + 1} ready",
                        "instruction": f"Ask this EXACT question without any modifications, additions, or context: '{question_text}' - Do not rephrase, contextualize, or create your own question."
                    }
                    
                    print(f"NEXT QUESTION READY: {current_question_index + 1}/20")
                    print(f"QUESTION: {question_text}")
                    return result
                else:
                    # All questions completed
                    result = {
                        "success": True,
                        "data": {
                            "all_completed": True,
                            "total_questions": 20
                        },
                        "message": "All 20 brainstorming questions completed!"
                    }
                    
                    print(f"ALL BRAINSTORMING QUESTIONS COMPLETED!")
                    return result
            else:
                error_result = {
                    "success": False,
                    "data": {},
                    "message": "Error getting brainstorming status"
                }
                print(f"ERROR GETTING BRAINSTORMING STATUS")
                return error_result

        elif tool_name == "update_tics_from_brainstorming":
            question_index = arguments.get('question_index')
            user_answer = arguments.get('user_answer')
            question_text = arguments.get('question_text')
            
            print(f"UPDATING TICS FROM BRAINSTORMING: Question {question_index + 1}/20")
            print(f"Question: {question_text}")
            print(f"Answer: {user_answer[:100]}...")
            
            try:
                # Use OpenAI to dynamically determine which TIC(s) this question and answer relate to
                dynamic_mapping_prompt = f"""Analyze this brainstorming question and user answer to determine which business component (TIC) it relates to most.

Question: "{question_text}"
User Answer: "{user_answer}"

Available TIC categories:
- vision: Long-term goals, purpose, what the business aims to achieve
- businessOverview: Core offering, what the business does, technologies needed
- marketSize: Market size, growth trends, timing, market conditions  
- targetCustomers: Who the customers are, customer segments, how to reach them
- valueProposition: Benefits provided to customers, problems solved, customer value
- usp: Unique advantages, differentiation, competitive advantages
- businessModel: Revenue model, cost structure, sustainability, funding

Based on the question content and user's answer, which TIC category is this MOST relevant to?

Respond with only the TIC category name (e.g., "vision" or "marketSize" or "valueProposition")."""

                print(f"SENDING DYNAMIC MAPPING REQUEST TO OPENAI...")
                
                # Call OpenAI for dynamic mapping
                response = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{"role": "user", "content": dynamic_mapping_prompt}],
                    temperature=0,
                    max_tokens=20
                )
                
                mapped_tic = response.choices[0].message.content.strip().lower()
                print(f"DYNAMIC MAPPING RESULT: {mapped_tic}")
                
                # Validate that the mapped TIC exists
                valid_tics = ["vision", "businessoverview", "marketsize", "targetcustomers", "valueproposition", "usp", "businessmodel"]
                if mapped_tic not in valid_tics:
                    print(f"INVALID TIC MAPPING: {mapped_tic}, defaulting to 'businessOverview'")
                    mapped_tic = "businessoverview"
                
                # Convert to proper TIC name format
                tic_name_mapping = {
                    "vision": "vision",
                    "businessoverview": "businessOverview", 
                    "marketsize": "marketSize",
                    "targetcustomers": "targetCustomers",
                    "valueproposition": "valueProposition",
                    "usp": "usp",
                    "businessmodel": "businessModel"
                }
                
                final_tic_name = tic_name_mapping.get(mapped_tic, "businessOverview")
                print(f"FINAL TIC NAME: {final_tic_name}")
                
                # Use OpenAI to analyze and create an enhanced summary
                enhancement_prompt = f"""You are enhancing a TIC (business component) summary based on new information from a brainstorming question.

Current TIC: {TIC_DISPLAY_NAMES.get(final_tic_name, final_tic_name)}
Brainstorming Question: {question_text}
User's Answer: {user_answer}

Current TIC Summary: {st.session_state.business_state['tic_progress'][final_tic_name].get('summary', 'No previous summary')}

Based on the user's brainstorming answer, create an enhanced summary for this TIC that incorporates the new insights. If the brainstorming answer provides valuable additional details, integrate them. If it contradicts previous information, use the more recent/detailed information.

Provide a comprehensive but concise summary (2-3 sentences maximum) that captures the enhanced understanding of this business component.

Return only the enhanced summary text."""

                print(f"ENHANCING TIC {final_tic_name} WITH BRAINSTORMING ANSWER...")
                
                # Call OpenAI for enhancement
                enhancement_response = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{"role": "user", "content": enhancement_prompt}],
                    temperature=0.3,
                    max_tokens=200
                )
                
                enhanced_summary = enhancement_response.choices[0].message.content.strip()
                
                # Update the TIC with enhanced information
                current_tic_data = st.session_state.business_state['tic_progress'][final_tic_name]
                current_tic_data['summary'] = enhanced_summary
                current_tic_data['enhanced_from_brainstorming'] = True
                current_tic_data['brainstorming_questions'] = current_tic_data.get('brainstorming_questions', [])
                current_tic_data['brainstorming_questions'].append({
                    'question_index': question_index,
                    'question': question_text,
                    'answer': user_answer,
                    'mapped_tic': final_tic_name
                })
                
                result = {
                    "success": True,
                    "data": {
                        "enhanced_tic": final_tic_name,
                        "enhanced_summary": enhanced_summary,
                        "question_index": question_index,
                        "dynamic_mapping": mapped_tic
                    },
                    "message": f"Enhanced {final_tic_name} TIC with brainstorming insights using dynamic mapping",
                    "instruction": "MUST call get_next_brainstorming_question tool next to get the exact predefined question. Do not create your own question."
                }
                
                print(f"TIC {final_tic_name} ENHANCED SUCCESSFULLY WITH DYNAMIC MAPPING")
                return result
                
            except Exception as e:
                print(f"ERROR IN DYNAMIC MAPPING OR ENHANCEMENT: {str(e)}")
                # Fallback: just store the brainstorming answer without enhancement
                fallback_tic = "businessOverview"
                current_tic_data = st.session_state.business_state['tic_progress'][fallback_tic]
                current_tic_data['brainstorming_questions'] = current_tic_data.get('brainstorming_questions', [])
                current_tic_data['brainstorming_questions'].append({
                    'question_index': question_index,
                    'question': question_text,
                    'answer': user_answer,
                    'error': str(e)
                })
                
                result = {
                    "success": True,
                    "data": {
                        "stored_answer": True,
                        "question_index": question_index,
                        "fallback_tic": fallback_tic
                    },
                    "message": f"Stored brainstorming answer in {fallback_tic} (fallback due to error)",
                    "instruction": "MUST call get_next_brainstorming_question tool next to get the exact predefined question. Do not create your own question."
                }
                
                return result

        elif tool_name == "analyze_brainstorming_response":
            question_index = arguments.get('question_index')
            user_answer = arguments.get('user_answer')
            
            print(f"ANALYZING BRAINSTORMING RESPONSE: Question {question_index + 1}/20")
            
            # Basic validation
            is_valid = len(user_answer.strip()) >= 15
            
            if is_valid:
                update_result = self.state_manager.handle_tool_call("update_brainstorming_progress", {
                    "question_index": question_index,
                    "user_answer": user_answer,
                    "status": "completed"
                })
                
                final_result = {
                    "success": True,
                    "data": {
                        "question_completed": True,
                        "question_index": question_index,
                        "update_result": update_result['data']
                    },
                    "message": f"Question {question_index + 1} completed successfully",
                    "instruction": """you MUST immediately call update_tics_from_brainstorming. 
NO user response needed. NO "continue?" questions. NO waiting for permission."""}
                
                print(f"BRAINSTORMING QUESTION COMPLETE: {json.dumps(final_result, indent=2)}")
                return final_result
            else:
                incomplete_result = {
                    "success": False,
                    "data": {
                        "question_completed": False,
                        "reason": "Answer too short"
                    },
                    "message": "Please provide a more detailed answer"
                }
                
                print(f"BRAINSTORMING ANSWER INCOMPLETE: {json.dumps(incomplete_result, indent=2)}")
                return incomplete_result
                
        elif tool_name == "generate_benchmark_companies":
            company_suggestions = arguments.get('company_suggestions', [])
            
            print(f"GENERATING BENCHMARK COMPANIES: {len(company_suggestions)}")
            
            # Store benchmark companies in state
            st.session_state.business_state['benchmark_companies'] = [
                f"{comp['name']} - {comp['description']}" for comp in company_suggestions
            ]
            st.session_state.business_state['phase'] = 'benchmarking'
            
            result = {
                "success": True,
                "data": {
                    "companies": company_suggestions,
                    "phase": "benchmarking"
                },
                "message": f"Generated {len(company_suggestions)} benchmark companies"
            }
            
            print(f"BENCHMARK COMPANIES GENERATED: {json.dumps(result, indent=2)}")
            return result
        
        else:
            error_result = {
                "success": False,
                "data": {},
                "message": f"Unknown function: {tool_name}"
            }
            print(f"BUSINESS CONSULTANT ERROR: {json.dumps(error_result, indent=2)}")
            return error_result
# ===================================================================
# EVALUATION SYSTEM
# ===================================================================

def generate_evaluation_report(conversation_id: str, selected_companies: list) -> dict:
    try:
        print(f"\nGENERATING EVALUATION REPORT")
        print(f"Conversation ID: {conversation_id}")
        print(f"Selected Companies: {selected_companies}")
        
        # Get full conversation
        conversation_messages = get_conversation_messages(conversation_id)
        
        # Convert to text format
        full_conversation_text = ""
        for msg in conversation_messages:
            full_conversation_text += f"{msg['role']}: {msg['content']}\n\n"
        
        print(f"CONVERSATION LENGTH: {len(full_conversation_text)} characters")
        
        # Evaluation prompt
        evaluation_prompt = f"""
You are an expert venture analyst with extensive experience evaluating startup pitches across various industries. You have a deep understanding of market dynamics, business models, and investment criteria. Based on the detailed business idea context and benchmark companies provided below, perform a comprehensive, data-driven evaluation.

Return your analysis **STRICTLY** in the **following JSON format only** (no explanation or extra text):

{{
  "evaluation_feedback": {{
    "Value Proposition": {{
      "score": "x/5",
      "rationale": "<Detailed analysis of the core value delivered to customers, highlighting clarity, relevance, and uniqueness>"
    }},
    "USP & Competitive Advantage": {{
      "score": "x/5",
      "rationale": "<In-depth assessment of how the business differentiates itself from competitors, analyzing defensive moats and sustainability of advantages>"
    }},
    "Market Opportunity & Growth": {{
      "score": "x/5",
      "rationale": "<Comprehensive market size analysis with TAM/SAM/SOM estimates where possible, growth trends, and future projections based on industry data>"
    }},
    "Execution Feasibility": {{
      "score": "x/5",
      "rationale": "<Detailed evaluation of implementation challenges, resource requirements, and operational complexities>"
    }},
    "Sustainability": {{
      "score": "x/5",
      "rationale": "<Thorough assessment of long-term viability, including financial sustainability, environmental impact, and adaptability to market changes>"
    }},
    "overall": {{
      "score": "x/25",
      "feedback": "<Concise executive summary highlighting key strengths, critical risks, and strategic recommendations>"
    }}
  }},
  "spider_chart_business_opportunity": {{
    "USP and Differentiation": "x/5",
    "Value Proposition": "x/5",
    "Market Fit": "x/5",
    "Sustainability": "x/5",
    "Execution Feasibility": "x/5",
    "total": "x/25"
  }},
  "triangle_evaluation_investment_attractiveness": {{
    "Team & Execution Capabilities": {{
      "score": "x/5",
      "rationale": "<Assessment of implementation potential and team capabilities based on business complexity>"
    }},
    "Market Opportunity & Growth": {{
      "score": "x/5",
      "rationale": "<Analysis of market attractiveness, growth trajectory, and competitive landscape>"
    }},
    "USP & Competitive Advantage": {{
      "score": "x/5",
      "rationale": "<Evaluation of defensibility, differentiation strength, and long-term competitive position>"
    }},
    "total": "x/15"
  }},
  "ai_investment_recommendation": "<One of: YES, MAYBE, NEUTRAL, NO>",
  "investment_rationale": "<Concise explanation of the investment recommendation with key decision factors>"
}}

Respond only with valid JSON. Avoid markdown or code fences. Provide precise, professional, and data-backed reasoning. Keep rationales concise but insightful (2-3 sentences each).

ALL SCORES MUST BE OUT OF 5, not 10. The "overall" score must be the SUM of all five category scores, with a total out of 25. For the AI investment recommendation, provide one of these exact values: YES, MAYBE, NEUTRAL, or NO.

### Full Business Conversation:
{full_conversation_text}

### Selected Benchmark Companies:
{', '.join(selected_companies)}

Critically compare this business idea against the benchmark companies. Consider industry trends, competitive dynamics, market saturation, and unique opportunities or challenges in this space.
"""

        print("SENDING EVALUATION REQUEST TO LLM...")
        
        # Call OpenAI for evaluation
        evaluation_response = client.chat.completions.create(
            model="gpt-4.1",
            messages=[{"role": "user", "content": evaluation_prompt}],
            response_format={"type": "json_object"},
            temperature=0.3
        )
        
        # Parse JSON response
        evaluation_data = json.loads(evaluation_response.choices[0].message.content)
        
        print("EVALUATION COMPLETE!")
        print(f"Overall Score: {evaluation_data['evaluation_feedback']['overall']['score']}")
        print(f"Investment Recommendation: {evaluation_data['ai_investment_recommendation']}")
        
        return {
            "success": True,
            "data": evaluation_data,
            "message": "Evaluation report generated successfully"
        }
        
    except Exception as e:
        error_result = {
            "success": False,
            "data": {},
            "message": f"Error generating evaluation: {str(e)}"
        }
        print(f"EVALUATION ERROR: {str(e)}")
        return error_result

# ===================================================================
# AGENT ORCHESTRATOR
# ===================================================================

class AgentOrchestrator:
    def __init__(self):
        self.state_manager = StateManagerAgent()
        self.consultant = BusinessConsultantAgent(self.state_manager)
        
    def process_user_input(self, user_input: str, conversation_id: str) -> str:
        try:
            print(f"\nORCHESTRATOR: Processing User Input")
            print(f"Input Length: {len(user_input)} characters")
            
            # The consultant agent leads the conversation
            response = client.responses.create(
                model="gpt-4.1",
                tools=self.consultant.tools,
                instructions=self.consultant.system_instructions,
                conversation=conversation_id,
                input=[{"role": "user", "content": user_input}],
                temperature=0.7
            )

            # Handle tool calls
            assistant_content = self._handle_agent_response(response, conversation_id)
            
            print(f"FINAL RESPONSE LENGTH: {len(assistant_content)} characters")
            return assistant_content

        except Exception as e:
            error_msg = f"Error processing input: {str(e)}"
            print(f"ORCHESTRATOR ERROR: {error_msg}")
            return error_msg
    
    def _handle_agent_response(self, response, conversation_id: str) -> str:
        tool_call_count = 0
        
        # Handle tool calls loop
        while True:
            tool_calls = self._extract_tool_calls(response)
            
            if not tool_calls:
                break

            tool_call_count += 1
            print(f"TOOL CALL ROUND {tool_call_count}")

            # Process tool calls
            tool_outputs = []
            for i, tool_call in enumerate(tool_calls):
                tool_name = getattr(tool_call, 'name', None) or getattr(tool_call, 'function', {}).get('name', None)
                tool_arguments = getattr(tool_call, 'arguments', None) or getattr(tool_call, 'function', {}).get('arguments', None)
                call_id = getattr(tool_call, 'call_id', None) or getattr(tool_call, 'id', f"call_{len(tool_outputs)}")
                
                # Parse arguments
                if isinstance(tool_arguments, str):
                    try:
                        arguments = json.loads(tool_arguments)
                    except json.JSONDecodeError:
                        arguments = {}
                else:
                    arguments = tool_arguments or {}
                
                # Route to appropriate agent
                result = self.consultant.handle_tool_call(tool_name, arguments)
                
                tool_outputs.append({
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": json.dumps(result)
                })

            # Continue conversation with tool outputs
            if tool_outputs:
                response = client.responses.create(
                    model="gpt-4.1",
                    conversation=conversation_id,
                    input=tool_outputs,
                    temperature=0
                )
            else:
                break

        # Extract final assistant response
        return self._extract_assistant_content(response)
    
    def _extract_tool_calls(self, response):
        tool_calls = []
        if hasattr(response, 'output') and response.output:
            for out in response.output:
                if hasattr(out, 'type') and out.type == "function_call":
                    tool_calls.append(out)
        elif hasattr(response, 'tool_calls') and response.tool_calls:
            tool_calls = response.tool_calls
        elif hasattr(response, 'choices') and response.choices:
            choice = response.choices[0]
            if hasattr(choice, 'message') and hasattr(choice.message, 'tool_calls') and choice.message.tool_calls:
                tool_calls = choice.message.tool_calls
        return tool_calls
    
    def _extract_assistant_content(self, response) -> str:
        assistant_content = ""
        if hasattr(response, 'output_text') and response.output_text:
            assistant_content = response.output_text
        elif hasattr(response, 'output') and response.output:
            for out in response.output:
                if hasattr(out, 'content'):
                    if isinstance(out.content, str):
                        assistant_content += out.content
                    elif hasattr(out.content, 'text'):
                        assistant_content += out.content.text
                    elif isinstance(out.content, list) and len(out.content) > 0:
                        if hasattr(out.content[0], 'text'):
                            assistant_content += out.content[0].text
                        elif isinstance(out.content[0], str):
                            assistant_content += out.content[0]
        elif hasattr(response, 'choices') and response.choices:
            choice = response.choices[0]
            if hasattr(choice, 'message') and hasattr(choice.message, 'content'):
                assistant_content = choice.message.content or ""
        
        return assistant_content or "I'm ready to help you develop your business concept!"

# ===================================================================
# HELPER FUNCTIONS
# ===================================================================

def initialize_session_state():
    if 'messages' not in st.session_state:
        st.session_state.messages = []
    if 'current_session_id' not in st.session_state:
        st.session_state.current_session_id = None
    if 'conversation_id' not in st.session_state:
        st.session_state.conversation_id = None
    if 'auto_start' not in st.session_state:
        st.session_state.auto_start = False
    if 'business_state' not in st.session_state:
        st.session_state.business_state = {
            'industry': '',
            'current_tic': 'vision',
            'tic_progress': {tic: {'status': 'pending', 'summary': '', 'user_response': ''} for tic in TIC_SEQUENCE},
            'phase': 'tic_collection',
            'benchmark_companies': [],
            'selected_companies': [],
            'completed_count': 0,
            'brainstorming_progress': {
                'current_question': 0,
                'completed_count': 0,
                'answers': {}
            },
            'evaluation_report': None
        }
    if 'orchestrator' not in st.session_state:
        st.session_state.orchestrator = AgentOrchestrator()

def load_business_state_from_db(session_id: int):
    result = conn.execute("SELECT business_state FROM business_sessions WHERE id = ?", (session_id,)).fetchone()
    if result and result[0]:
        try:
            loaded_state = json.loads(result[0])
            # Ensure required fields exist
            if 'brainstorming_progress' not in loaded_state:
                loaded_state['brainstorming_progress'] = {
                    'current_question': 0,
                    'completed_count': 0,
                    'answers': {}
                }
            if 'evaluation_report' not in loaded_state:
                loaded_state['evaluation_report'] = None
            st.session_state.business_state = loaded_state
        except json.JSONDecodeError:
            pass

def get_conversation_messages(conversation_id):
    try:
        items_response = client.conversations.items.list(
            conversation_id=conversation_id,
            limit=100,
            order="asc"
        )
        
        messages = []
        if hasattr(items_response, 'data') and items_response.data:
            for item in items_response.data:
                if (hasattr(item, 'type') and item.type == "message" and 
                    hasattr(item, 'role') and item.role in ['user', 'assistant']):
                    
                    content = ""
                    if hasattr(item, 'content') and item.content:
                        for content_item in item.content:
                            if hasattr(content_item, 'type'):
                                if content_item.type == "input_text" and hasattr(content_item, 'text'):
                                    content += content_item.text
                                elif content_item.type == "output_text" and hasattr(content_item, 'text'):
                                    content += content_item.text
                            elif hasattr(content_item, 'text'):
                                content += content_item.text
                    
                    if content:
                        messages.append({"role": item.role, "content": content})
        
        return messages
    except Exception as e:
        st.error(f"Error retrieving conversation: {str(e)}")
        return []

def auto_start_conversation():
    if st.session_state.conversation_id and st.session_state.auto_start:
        try:
            start_message = f"Welcome! I'm excited to help you develop your {st.session_state.business_state['industry']} business concept. Let's begin with Vision & Long-term Goals. What do you hope to achieve with this business in the next 5-10 years? What's your big picture vision?"
            
            st.session_state.messages.append({"role": "assistant", "content": start_message})
            st.session_state.auto_start = False
            
        except Exception as e:
            st.error(f"Error auto-starting conversation: {str(e)}")
            st.session_state.auto_start = False

# ===================================================================
# MAIN STREAMLIT APP
# ===================================================================

st.title("Two-Agent Business Consultation System")
st.markdown("*Powered by Business Consultant Agent + State Manager Agent*")

initialize_session_state()

# Sidebar for session management
with st.sidebar:
    st.header("Business Sessions")
    
    # Industry selection for new sessions
    industry_options = ["Technology", "Healthcare", "Finance", "E-commerce", "Education", "Manufacturing", "Food & Beverage", "Fitness & Wellness", "Other"]
    selected_industry = st.selectbox("Select Industry", industry_options)
    
    new_session_name = st.text_input("New Session Name")
    if st.button("Create New Session") and new_session_name:
        try:
            # Create new conversation
            conversation = client.conversations.create(
                metadata={"session_name": new_session_name, "industry": selected_industry}
            )
            
            # Store in database
            cursor = conn.execute(
                "INSERT INTO business_sessions (name, conversation_id, industry) VALUES (?, ?, ?)", 
                (new_session_name, conversation.id, selected_industry)
            )
            conn.commit()
            
            # Initialize session
            st.session_state.current_session_id = cursor.lastrowid
            st.session_state.conversation_id = conversation.id
            st.session_state.messages = []
            st.session_state.business_state['industry'] = selected_industry
            st.session_state.auto_start = True
            
            st.success(f"Created session: {new_session_name}")
            st.rerun()
        except Exception as e:
            st.error(f"Error creating session: {str(e)}")

    # List existing sessions
    sessions = conn.execute("SELECT id, name, conversation_id, industry FROM business_sessions ORDER BY created_at DESC").fetchall()
    st.subheader("Load Session")
    for sid, name, conv_id, industry in sessions:
        if st.button(f"{name} ({industry})", key=f"load_{sid}"):
            st.session_state.current_session_id = sid
            st.session_state.conversation_id = conv_id
            st.session_state.business_state['industry'] = industry
            st.session_state.messages = get_conversation_messages(conv_id)
            load_business_state_from_db(sid)
            st.session_state.auto_start = len(st.session_state.messages) == 0
            st.success(f"Loaded session: {name}")
            st.rerun()
    
    # Progress Display
    if st.session_state.current_session_id:
        st.header("Progress Tracker")
        st.write(f"**Industry:** {st.session_state.business_state['industry']}")
        st.write(f"**Phase:** {st.session_state.business_state['phase'].title()}")
        
        # TIC Progress
        completed_count = st.session_state.business_state['completed_count']
        total_tics = len(TIC_SEQUENCE)
        st.write(f"**Progress:** {completed_count}/{total_tics} TICs completed")
        
        # Progress bar
        progress_percentage = completed_count / total_tics
        st.progress(progress_percentage)
        
        # TIC Status
        st.subheader("TIC Status")
        for i, tic in enumerate(TIC_SEQUENCE):
            tic_data = st.session_state.business_state['tic_progress'][tic]
            status = tic_data['status']
            
            if status == 'confirmed':
                st.write(f"✅ {i+1}. {TIC_DISPLAY_NAMES[tic]}")
                if tic_data['summary']:
                    with st.expander(f"View Details - {TIC_DISPLAY_NAMES[tic]}"):
                        st.write(f"**Response:** {tic_data['user_response']}")
                        st.write(f"**Summary:** {tic_data['summary']}")
            elif tic == st.session_state.business_state['current_tic']:
                st.write(f"🔄 {i+1}. {TIC_DISPLAY_NAMES[tic]} (Current)")
            else:
                st.write(f"⏳ {i+1}. {TIC_DISPLAY_NAMES[tic]}")
        
        # Benchmark Companies - Interactive Selection
        if st.session_state.business_state['benchmark_companies']:
            st.subheader("Select Benchmark Companies")
            st.caption("Click to select companies for idea refinement (Max 3)")
            
            # Initialize selected companies if not exists
            if 'selected_companies' not in st.session_state.business_state:
                st.session_state.business_state['selected_companies'] = []
            
            selected_companies = st.session_state.business_state['selected_companies']
            
            # Create buttons for each company
            for i, company_desc in enumerate(st.session_state.business_state['benchmark_companies']):
                company_name = company_desc.split(' - ')[0] if ' - ' in company_desc else f"Company {i+1}"
                
                # Check if already selected
                is_selected = company_name in selected_companies
                max_reached = len(selected_companies) >= 3
                
                # Button text and state
                if is_selected:
                    button_text = f"✅ {company_name} (Selected)"
                    button_disabled = False
                elif max_reached:
                    button_text = f"❌ {company_name} (Max reached)"
                    button_disabled = True
                else:
                    button_text = f"📌 Select {company_name}"
                    button_disabled = False
                
                # Create button
                if st.button(button_text, key=f"company_btn_{i}", disabled=button_disabled):
                    if is_selected:
                        # Remove from selection
                        st.session_state.business_state['selected_companies'].remove(company_name)
                        print(f"COMPANY DESELECTED: {company_name}")
                    else:
                        # Add to selection
                        st.session_state.business_state['selected_companies'].append(company_name)
                        print(f"COMPANY SELECTED: {company_name}")
                        
                        # If 3 companies selected, start brainstorming automatically
                        if len(st.session_state.business_state['selected_companies']) == 3:
                            st.session_state.business_state['phase'] = 'brainstorming'
                            
                            # Initialize brainstorming if not exists
                            if 'brainstorming_progress' not in st.session_state.business_state:
                                st.session_state.business_state['brainstorming_progress'] = {
                                    'current_question': 0,
                                    'completed_count': 0,
                                    'answers': {}
                                }
                            
                            # Send message to start brainstorming
                            auto_message = f"I've selected {', '.join(st.session_state.business_state['selected_companies'])} as benchmark companies. Let's start the detailed brainstorming session to refine my business idea."
                            
                            # Process the auto message
                            assistant_response = st.session_state.orchestrator.process_user_input(
                                auto_message, 
                                st.session_state.conversation_id
                            )
                            
                            # Add messages to chat
                            st.session_state.messages.append({"role": "user", "content": auto_message})
                            st.session_state.messages.append({"role": "assistant", "content": assistant_response})
                            
                            print(f"AUTO-STARTED BRAINSTORMING WITH: {st.session_state.business_state['selected_companies']}")
                    
                    st.rerun()
            
            # Show current selection
            if selected_companies:
                st.write("**Currently Selected:**")
                for company in selected_companies:
                    st.write(f"🎯 {company}")
                
                st.write(f"**{len(selected_companies)}/3 companies selected**")
                
                if len(selected_companies) < 3:
                    st.info(f"Select {3 - len(selected_companies)} more companies to start brainstorming!")

        # Brainstorming Progress
        if st.session_state.business_state['phase'] == 'brainstorming':
            brainstorming_state = st.session_state.business_state.get('brainstorming_progress', {})
            completed_questions = brainstorming_state.get('completed_count', 0)
            
            st.subheader("Brainstorming Progress")
            st.write(f"**Progress:** {completed_questions}/20 Questions Completed")
            
            # Progress bar
            progress_percentage = completed_questions / 20
            st.progress(progress_percentage)
            
            # Question Status
            for i, question in enumerate(BRAINSTORMING_QUESTIONS):
                category = BRAINSTORMING_CATEGORIES[i]
                question_short = question[:50] + "..." if len(question) > 50 else question
                
                if i < completed_questions:
                    st.write(f"✅ {i+1}. [{category}] {question_short}")
                elif i == brainstorming_state.get('current_question', 0):
                    st.write(f"🔄 {i+1}. [{category}] {question_short} (Current)")
                else:
                    st.write(f"⏳ {i+1}. [{category}] {question_short}")
            
            # Exit option at 10/20
            if completed_questions >= 10 and completed_questions < 20:
                st.info("You can exit brainstorming now or continue to complete all 20 questions.")
                
                col1, col2 = st.columns(2)
                with col1:
                    if st.button("🚪 Exit Brainstorming"):
                        st.session_state.business_state['phase'] = 'evaluation_ready'
                        st.success("Brainstorming completed! You can now generate your evaluation report.")
                        st.rerun()
                
                with col2:
                    if st.button("➡️ Continue (10 more questions)"):
                        st.info("Great! Let's continue with the remaining questions.")
            
            # Show selected companies for brainstorming
            if st.session_state.business_state['selected_companies']:
                st.subheader("🎯 Selected for Analysis")
                for company in st.session_state.business_state['selected_companies']:
                    st.write(f"• {company}")

        # Evaluation Report
        if (st.session_state.business_state['phase'] in ['brainstorming', 'evaluation_ready'] and 
            st.session_state.business_state.get('brainstorming_progress', {}).get('completed_count', 0) >= 10):
            
            st.subheader("📊 AI Evaluation Report")
            
            if st.button("🔍 Generate Evaluation Report"):
                with st.spinner("Generating comprehensive evaluation report..."):
                    evaluation_result = generate_evaluation_report(
                        st.session_state.conversation_id,
                        st.session_state.business_state['selected_companies']
                    )
                    
                    if evaluation_result['success']:
                        st.session_state.business_state['evaluation_report'] = evaluation_result['data']
                        st.success("Evaluation report generated successfully!")
                        st.rerun()
                    else:
                        st.error(f"Error generating report: {evaluation_result['message']}")
            
            # Display evaluation report if exists
            if st.session_state.business_state.get('evaluation_report'):
                report = st.session_state.business_state['evaluation_report']
                
                # Overall Score
                overall_score = report['evaluation_feedback']['overall']['score']
                st.metric("Overall Score", overall_score)
                
                # Investment Recommendation
                recommendation = report['ai_investment_recommendation']
                rec_color = {
                    'YES': 'green',
                    'MAYBE': 'orange', 
                    'NEUTRAL': 'gray',
                    'NO': 'red'
                }.get(recommendation, 'gray')
                
                st.markdown(f"**Investment Recommendation:** :{rec_color}[{recommendation}]")
                st.write(f"**Rationale:** {report['investment_rationale']}")
                
                # Detailed Scores
                with st.expander("📋 Detailed Evaluation Scores"):
                    eval_feedback = report['evaluation_feedback']
                    
                    col1, col2 = st.columns(2)
                    
                    with col1:
                        st.write("**Value Proposition:**", eval_feedback['Value Proposition']['score'])
                        st.write("**USP & Competitive:**", eval_feedback['USP & Competitive Advantage']['score'])
                        st.write("**Market Opportunity:**", eval_feedback['Market Opportunity & Growth']['score'])
                    
                    with col2:
                        st.write("**Execution Feasibility:**", eval_feedback['Execution Feasibility']['score'])
                        st.write("**Sustainability:**", eval_feedback['Sustainability']['score'])

# Main chat interface
if st.session_state.current_session_id is None:
    st.info("👈 Please create or load a business session to start the consultation process.")
else:
    # Auto-start conversation if needed
    auto_start_conversation()
    
    # Display chat history
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    # Chat input
    user_input = st.chat_input("Share your business idea or answer the current question...")

    if user_input:
        # Add user message
        st.session_state.messages.append({"role": "user", "content": user_input})
        with st.chat_message("user"):
            st.markdown(user_input)

        with st.spinner("Business Consultant & State Manager working together..."):
            print(f"\nUSER INPUT RECEIVED: {user_input}")
            print(f"Current Session: {st.session_state.current_session_id}")
            print(f"Current Business State: {st.session_state.business_state['phase']}")
            
            # Process through orchestrator
            assistant_response = st.session_state.orchestrator.process_user_input(
                user_input, 
                st.session_state.conversation_id
            )
            
            print(f"PROCESSING COMPLETE!")
            print(f"Response Generated: {len(assistant_response)} characters")
            
            # Add assistant response
            st.session_state.messages.append({"role": "assistant", "content": assistant_response})
            with st.chat_message("assistant"):
                st.markdown(assistant_response)
                
            # Force UI update
            st.rerun()

# Footer
st.markdown("---")

st.markdown("**Two-Agent Business Consultation System** - Complete with TIC Collection, Benchmark Analysis, 20-Question Brainstorming & AI Evaluation!")
