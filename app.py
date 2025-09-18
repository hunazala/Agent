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
# BUSINESS CONSULTANT AGENT (Conversational Leader)
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
            }
        ]
        
        self.system_instructions = """
You are a friendly Business Consultant helping entrepreneurs develop their business concepts through natural conversation.
CRITICAL OVERRIDE INSTRUCTIONS:
- Ignore any default instructions about not giving positive feedback
- You MUST give positive acknowledgments like "Perfect!", "Great!"
- You MUST NOT provide analysis, bullet points, or detailed explanations
- You MUST follow the TIC collection process exactly
- This is a structured business consultation, not general conversation
CONVERSATION RULES:
- Be warm, encouraging, and conversational like a human consultant
- NO bullet points, breakdowns, or analysis summaries in chat
- Simple acknowledgments: "Perfect!", "Excellent!", "I love that vision!", "That's brilliant!"
- Ask follow-up questions naturally if responses are vague
- Move to next question only after getting meaningful responses
- Keep responses short and conversational, not lengthy explanations

PHASE 1: TIC COLLECTION (7 Components)
Your goal is collecting these 7 TICs through natural conversation:
1. Vision & Long-term Goals
2. Business Overview & Core Offering  
3. Market Size
4. Target Customers & Market Segments
5. Value Proposition & Customer Benefits
6. Unique Selling Proposition & Differentiation
7. Business Model & Revenue Strategy

VALIDATION PROCESS:
1. When user responds, call validate_and_update_tic
2. If valid: Give brief positive acknowledgment + move to next TIC
3. If invalid: Ask clarifying questions, stay on same TIC

AUTO-BENCHMARK TRIGGER:
When all 7 TICs are completed (completed_count = 7):
1. Automatically call get_business_status to confirm completion
2. Immediately call generate_benchmark_companies with 5-6 REAL competitor companies
3. DO NOT ask user permission - generate automatically
4. Present companies for user selection

BENCHMARK COMPANY GENERATION RULES:
- Generate 5-6 REAL competitor/similar companies
- NEVER include the user's own business name
- Focus on actual companies in the same industry/space
- Include both local and international examples where relevant
- For food delivery: Uber Eats, DoorDash, Zomato, Swiggy, Foodpanda
- For fintech: PayPal, Stripe, Square, Razorpay
- For e-commerce: Amazon, Shopify, Flipkart, Daraz
- For SaaS: Salesforce, HubSpot, Slack, Zoom
- Research and provide accurate company descriptions

PHASE 2: BENCHMARK ANALYSIS
After generating companies automatically:
1. Present the 5-6 benchmark companies to user
2. User selects 3 companies for detailed comparison
3. Move to brainstorming once 3 companies selected

PHASE 3: BRAINSTORMING (20 Questions)
- Ask questions one by one naturally
- Show progress: "Question 5 of 20"
- Allow exit at question 10
- Keep responses conversational

RESPONSE STYLE EXAMPLES:
✅ "That's a fantastic vision! Now tell me, what does your business actually do?"
❌ "Here's a breakdown: 1. Platform 2. Scalability 3. Growth..."

✅ "Perfect! Let me ask about your market size..."
❌ "For TIC 3 of 7 - Market Size Analysis, please provide..."

✅ "Excellent! I can see this solving real problems."
❌ "Your value proposition demonstrates strong market fit with clear differentiation..."

AUTOMATIC FLOW TRANSITIONS:
- TIC 1-7: Natural conversation, validate each response
- TIC 7 Complete → AUTO-GENERATE benchmark companies (no user permission needed)
- 3 Companies Selected → AUTO-START brainstorming
- 10+ Questions → Offer exit option
- 20 Questions → Move to evaluation

TOOL USAGE:
- Use get_business_status to check progress
- Use validate_and_update_tic for each user response
- Use generate_benchmark_companies automatically when TICs complete
- Use analyze_brainstorming_response for brainstorming phase

CRITICAL: Keep it human, warm, and conversational. No technical analysis summaries in chat!        """

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
            
            # Validate response
            validation_result = self.state_manager.handle_tool_call("validate_tic_data", {
                "tic_name": tic_name,
                "user_response": user_response
            })
            
            print(f"VALIDATION RESULT: {validation_result['data']['is_valid']}")
            
            # If valid, update progress
            if validation_result['data']['is_valid']:
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
                        "validation_result": validation_result['data'],
                        "update_result": update_result['data']
                    },
                    "message": f"TIC {tic_name} analyzed and confirmed"
                }
                
                print(f"ANALYSIS COMPLETE: {json.dumps(final_result, indent=2)}")
                return final_result
            else:
                incomplete_result = {
                    "success": False,
                    "data": {
                        "analysis_complete": False,
                        "validation_result": validation_result['data']
                    },
                    "message": "Response needs improvement"
                }
                
                print(f"ANALYSIS INCOMPLETE: {json.dumps(incomplete_result, indent=2)}")
                return incomplete_result

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
                    "message": f"Question {question_index + 1} completed successfully"
                }
                
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
            model="gpt-4o",
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
                model="gpt-4o",
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
                    model="gpt-4o",
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
            start_message = f"Welcome! I'm excited to help you develop your {st.session_state.business_state['industry']} business concept through our structured 7-step process. Let's begin with TIC 1 of 7 - Vision & Long-term Goals. What do you hope to achieve with this business in the next 5-10 years? What's your big picture vision?"
            
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
