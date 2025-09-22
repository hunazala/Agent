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

# AI-Generated Dynamic Questions (max 20 questions)
# Questions are now generated dynamically based on:
# - TIC responses (7 business components)
# - Selected benchmark companies
# - Previous answers and context
# - 7 evaluation criteria: Vision, Market Opportunity, USP & Competitive Advantage,
#   Value Proposition, Sustainability, Execution Feasibility
# - Progress-based focus areas for strategic questioning

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
                "description": "Update brainstorming question progress with dynamic question data",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "question_index": {"type": "integer"},
                        "user_answer": {"type": "string"},
                        "status": {"type": "string", "enum": ["completed"]},
                        "question_text": {"type": "string", "description": "The actual question text that was asked"},
                        "question_category": {"type": "string", "description": "Category of the question (e.g., Vision, Market, USP, etc.)"}
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
            question_text = arguments.get('question_text', 'Dynamic AI-generated question')
            question_category = arguments.get('question_category', 'General')

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

            # Update the answer with dynamic question data
            brainstorming_state['answers'][str(question_index)] = {
                'question': question_text,
                'category': question_category,
                'answer': user_answer,
                'timestamp': datetime.now().isoformat()
            }

            if status == 'completed':
                # Increment completed count properly - it should be question_index + 1
                brainstorming_state['completed_count'] = question_index + 1

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
# BUSINESS CONSULTANT AGENT (Conversational Leader) - UPDATED
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
                "name": "provide_help",
                "description": "Provide conversational help when user asks questions or needs clarification",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "user_question": {"type": "string"},
                        "help_type": {"type": "string", "enum": ["explanation", "clarification", "guidance", "example"]},
                        "context": {"type": "string"}
                    },
                    "required": ["user_question", "help_type"],
                    "additionalProperties": False
                }
            }
        ]
        
        self.system_instructions = """
You are a TIC Collection Agent focused on collecting 7 business components through conversation.

PHASE 1: TIC COLLECTION (7 Components) - YOUR MAIN JOB
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

HANDLING USER QUESTIONS/CONFUSION:
- If user asks questions or seems confused, call provide_help tool first
- Provide explanations, examples, or clarification as needed
- Then continue with TIC collection
- Be conversational and helpful like a consultant

RESPONSE VALIDATION RULES:
- Answers must be SPECIFIC and COMPLETE (not vague like "better rates")
- If answer lacks core details, ask clarifying questions: "Could you be more specific about..."
- Don't accept generic responses - demand concrete information
- Examples of INVALID responses: "better services", "competitive prices", "good quality"
- Examples of VALID responses: "We provide accounting software for small restaurants"

MANDATORY TOOL WORKFLOW:
1. If user asks question or seems confused → call provide_help tool
2. When user answers TIC question → ALWAYS call analyze_user_response
3. If response valid → TIC marked complete, move to next TIC
4. If response invalid → ask clarification question
5. When ALL 7 TICs complete → AUTOMATICALLY call generate_benchmark_companies

CRITICAL: ANALYSIS_SUMMARY CREATION REQUIREMENTS:
When calling analyze_user_response, create comprehensive analysis_summary that:
- Captures the USER'S VISION and passion behind their business idea
- Focuses on their unique perspective and how they see their business impacting the world
- Includes specific details that reflect their entrepreneurial vision and motivation
- Uses their own language and terminology where possible
- Highlights what makes their approach unique or innovative
- Connects to their personal values and goals
- Creates a narrative that shows their business evolution and thinking
- Makes the summary engaging and reflective of their actual vision, not generic business language

Example GOOD analysis_summary:
"The founder envisions democratizing healthcare access through AI, driven by personal experience with healthcare gaps in underserved communities. Their vision combines technological innovation with social impact, aiming to become the primary preventive healthcare platform globally while maintaining strict privacy standards and clinical accuracy."

Example POOR analysis_summary:
"Business provides healthcare solutions using AI technology for customers."

TIC QUESTIONS TO ASK:
1. Vision: "What do you hope to achieve with this business in 5-10 years?"
2. Business Overview: "What exactly does your business do? What's your core offering?"  
3. Market Size: "How big is your target market? Any estimates?"
4. Target Customers: "Who exactly are your customers? What segments?"
5. Value Proposition: "What key benefits do you provide customers?"
6. USP: "What makes you different from competitors? What's unique?"
7. Business Model: "How do you make money? What's your revenue model?"

TOOL CALLING REQUIREMENTS:
- ALWAYS call provide_help when user asks questions or needs guidance
- ALWAYS call analyze_user_response when user answers TIC questions
- ALWAYS call get_business_status to check current progress  
- ALWAYS call generate_benchmark_companies when all 7 TICs done

RESPONSE EXAMPLES:
✅ "Perfect! What exactly does your business do?"
✅ "Got it! How big is your target market?"
✅ "Could you be more specific about what services you actually provide?"
❌ "Great idea! Here are some steps: 1. Market research 2. Business plan..."
❌ Any business advice or analysis in chat

Remember: 
- Tools update sidebar and progress
- Chat responses are brief questions only  
- Automatic transitions between phases
- No business advice in chat responses
- Demand specific, complete answers before moving forward
- Never mention TIC numbers in conversation
- Be helpful and conversational when user needs guidance
"""

    def _analyze_summary_completeness(self, tic_name: str, user_response: str, analysis_summary: str) -> bool:
        """
        Use OpenAI to analyze if the response was complete and specific enough.
        Returns True if complete, False if needs clarification.
        Now includes conversation history to prevent infinite questioning loops.
        """
        try:
            tic_display_name = TIC_DISPLAY_NAMES.get(tic_name, tic_name)
            
            # Get conversation history for this TIC to provide context
            conversation_history = ""
            if st.session_state.messages:
                # Get last 10 messages to understand the conversation flow
                recent_messages = st.session_state.messages[-10:]
                
                # Filter messages related to current TIC or general conversation
                relevant_messages = []
                for msg in recent_messages:
                    content = msg.get('content', '').lower()
                    # Include if it mentions the TIC topic or seems like clarification
                    if (tic_display_name.lower() in content or 
                        any(keyword in content for keyword in ['specific', 'clarify', 'more details', 'what exactly', 'could you'])):
                        relevant_messages.append(f"{msg['role']}: {msg['content']}")
                
                if relevant_messages:
                    conversation_history = "\n".join(relevant_messages[-6:])  # Last 6 relevant messages
            
            # Count how many times we've asked for clarification on this TIC
            clarification_count = 0
            tic_data = st.session_state.business_state['tic_progress'].get(tic_name, {})
            clarification_count = tic_data.get('clarification_attempts', 0)
            
            # If we've already asked for clarification 2+ times, be more lenient
            if clarification_count >= 2:
                print(f"CLARIFICATION LIMIT REACHED: {clarification_count} attempts for {tic_name}")
                # After 2 attempts, automatically accept any response with at least 5 words
                word_count = len(user_response.split())
                if word_count >= 5:
                    print(f"AUTO-ACCEPTING AFTER 2 CLARIFICATIONS: {word_count} words")
                    # Reset clarification count and accept
                    tic_data['clarification_attempts'] = 0
                    st.session_state.business_state['tic_progress'][tic_name] = tic_data
                    return True
            
            analysis_prompt = f"""You are analyzing whether a user's response to a business question was complete and specific enough.

TIC Question: {tic_display_name}
User Response: "{user_response}"
Analysis Summary: "{analysis_summary}"

CONVERSATION HISTORY (for context):
{conversation_history if conversation_history else "No previous conversation context available"}

CLARIFICATION ATTEMPTS: {clarification_count} (0=first attempt, 1+=already asked for clarification)

IMPORTANT CONTEXT:
- If clarification has been requested {clarification_count} times already, be MORE LENIENT
- Look at conversation history to see if user has been progressively providing more detail
- Don't keep asking for the same type of clarification repeatedly
- If the user is clearly making an effort to provide details, accept reasonable responses

Based on the analysis summary AND conversation context, determine if the user's response was:
- COMPLETE: Specific enough, addresses the question requirements, or shows good faith effort after previous clarifications
- INCOMPLETE: Still genuinely vague/missing key information AND this is a reasonable first/second clarification request

The analysis summary will often indicate if something is missing, but consider the conversation flow and clarification history.

Respond with only one word: "COMPLETE" or "INCOMPLETE"
"""
            
            print(f"SENDING COMPLETENESS CHECK TO OPENAI WITH HISTORY...")
            print(f"Clarification attempts: {clarification_count}")
            print(f"Conversation history length: {len(conversation_history)}")
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
            
            # Update clarification attempt count if we're going to ask for more clarification
            if not is_complete:
                tic_data['clarification_attempts'] = clarification_count + 1
                st.session_state.business_state['tic_progress'][tic_name] = tic_data
                print(f"INCOMPLETE RESPONSE DETECTED BY OPENAI: {analysis_summary}")
                print(f"CLARIFICATION ATTEMPTS NOW: {clarification_count + 1}")
            else:
                # Reset clarification count on successful completion
                tic_data['clarification_attempts'] = 0
                st.session_state.business_state['tic_progress'][tic_name] = tic_data
                print(f"RESPONSE ACCEPTED - CLARIFICATION COUNT RESET")
            
            return is_complete
            
        except Exception as e:
            print(f"ERROR IN OPENAI COMPLETENESS CHECK: {str(e)}")
            # Fallback to simple check if OpenAI fails
            clarification_count = st.session_state.business_state['tic_progress'].get(tic_name, {}).get('clarification_attempts', 0)
            
            # If we've tried multiple times, be lenient in fallback
            if clarification_count >= 2:
                return len(user_response.strip()) >= 20
            
            # Standard fallback logic
            incomplete_keywords = ["lacks", "missing", "vague", "unclear", "does not", "doesn't", "no information", "incomplete"]
            summary_lower = analysis_summary.lower()
            
            for keyword in incomplete_keywords:
                if keyword in summary_lower:
                    print(f"FALLBACK: Found incomplete keyword '{keyword}'")
                    # Update clarification count in fallback too
                    tic_data = st.session_state.business_state['tic_progress'].get(tic_name, {})
                    tic_data['clarification_attempts'] = clarification_count + 1
                    st.session_state.business_state['tic_progress'][tic_name] = tic_data
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
            
            # Check if response is complete based on summary analysis using OpenAI
            is_complete = self._analyze_summary_completeness(tic_name, user_response, analysis_summary)
            
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
                    "instruction": "ask next TIC question only. NO business advice.and do not say anything else like thank you and do not give any summary or anything"
                }
                
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
                    "instruction": "Ask clarifying question to get more specific details. Stay on current TIC.the clarifying question should be concise."
                }
                
                print(f"ANALYSIS INCOMPLETE: {json.dumps(incomplete_result, indent=2)}")
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
        
        elif tool_name == "provide_help":
            user_question = arguments.get('user_question', '')
            help_type = arguments.get('help_type', 'explanation')
            context = arguments.get('context', '')
            
            print(f"PROVIDING HELP: {help_type} for '{user_question}'")
            
            # This tool just acknowledges that help was requested
            # The actual helpful response will be in the LLM's chat response
            result = {
                "success": True,
                "data": {
                    "help_provided": True,
                    "help_type": help_type,
                    "user_question": user_question
                },
                "message": "Help provided to user"
            }
            
            print(f"HELP PROVIDED: {json.dumps(result, indent=2)}")
            return result
        
        else:
            error_result = {
                "success": False,
                "data": {},
                "message": f"Unknown function: {tool_name}"
            }
            print(f"BUSINESS CONSULTANT ERROR: {json.dumps(error_result, indent=2)}")
            return error_result

    def _generate_first_question(self) -> str:
        """Generate the first brainstorming question based on TIC context and benchmark companies"""
        try:
            # Get business context from TICs
            tic_progress = st.session_state.business_state['tic_progress']
            industry = st.session_state.business_state['industry']
            selected_companies = st.session_state.business_state.get('selected_companies', [])

            # Build context from completed TICs
            business_context = f"Industry: {industry}\n"
            for tic_name, tic_data in tic_progress.items():
                if tic_data['status'] == 'confirmed' and tic_data['summary']:
                    display_name = TIC_DISPLAY_NAMES.get(tic_name, tic_name)
                    business_context += f"{display_name}: {tic_data['summary']}\n"

            # Add benchmark companies context
            benchmark_context = f"Selected benchmark companies: {', '.join(selected_companies)}"

            question_prompt = f"""You are an expert business analyst conducting a deep-dive evaluation. Based on the business idea and benchmark companies below, generate the FIRST strategic question to begin a comprehensive 20-question evaluation.

Business Context:
{business_context}

{benchmark_context}

Generate the most important first question that will:
1. Build on the existing TIC information
2. Consider the competitive landscape from benchmark companies
3. Focus on the 7 evaluation criteria: Vision, Market Opportunity, USP & Competitive Advantage, Value Proposition, Sustainability, or Execution Feasibility
4. Be specific and strategic (not generic)
5. Help evaluate investment potential

Return only the question text, no numbering or formatting."""

            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": question_prompt}],
                temperature=0.3
            )

            return response.choices[0].message.content.strip()

        except Exception as e:
            print(f"Error generating first question: {str(e)}")
            # Fallback to a solid first question
            return "What specific value does this business idea aim to bring to the world, and why is now the right time to pursue this opportunity?"

    def _generate_next_question(self, completed_count: int) -> str:
        """Generate the next question based on previous answers and business context"""
        try:
            # Get business context
            tic_progress = st.session_state.business_state['tic_progress']
            industry = st.session_state.business_state['industry']
            selected_companies = st.session_state.business_state.get('selected_companies', [])
            brainstorming_progress = st.session_state.business_state.get('brainstorming_progress', {})
            previous_answers = brainstorming_progress.get('answers', {})

            # Build comprehensive context
            business_context = f"Industry: {industry}\n"
            for tic_name, tic_data in tic_progress.items():
                if tic_data['status'] == 'confirmed' and tic_data['summary']:
                    display_name = TIC_DISPLAY_NAMES.get(tic_name, tic_name)
                    business_context += f"{display_name}: {tic_data['summary']}\n"

            # Add previous Q&A context
            qa_context = "\nPrevious Questions & Answers:\n"
            for i in range(completed_count):
                if i in previous_answers:
                    qa_context += f"Q{i+1}: {previous_answers[i].get('question', 'Unknown')}\n"
                    qa_context += f"A{i+1}: {previous_answers[i].get('answer', 'No answer')}\n\n"

            # Determine focus area based on progress
            focus_guidance = self._get_question_focus_guidance(completed_count)

            question_prompt = f"""You are an expert business analyst conducting a deep-dive evaluation. Based on the business context, previous Q&A, and benchmark companies, generate the NEXT strategic question (Question {completed_count + 1} of 20).

Business Context:
{business_context}

Selected Benchmark Companies: {', '.join(selected_companies)}

{qa_context}

Focus for this question: {focus_guidance}

Generate a strategic question that:
1. Builds on previous answers and identified gaps
2. Considers the competitive landscape and benchmark companies
3. Focuses on the 7 evaluation criteria: Vision, Market Opportunity, USP & Competitive Advantage, Value Proposition, Sustainability, or Execution Feasibility
4. Is specific and contextual (not generic)
5. Helps evaluate investment potential and business viability
6. Avoids repeating topics already well-covered

Return only the question text, no numbering or formatting."""

            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": question_prompt}],
                temperature=0.4
            )

            return response.choices[0].message.content.strip()

        except Exception as e:
            print(f"Error generating next question: {str(e)}")
            # Fallback questions based on progress
            fallback_questions = [
                "How does your solution address customer pain points better than existing alternatives?",
                "What specific market trends support the timing of this business opportunity?",
                "What key resources and capabilities do you need to execute this vision successfully?",
                "How will you differentiate from competitors in a sustainable way?",
                "What assumptions about customer behavior need to be validated?",
                "What are the biggest risks to successful implementation and how will you mitigate them?",
                "How does this business model create long-term value for all stakeholders?",
                "What regulatory or compliance considerations could impact your business?",
                "How will you measure success and key performance indicators?",
                "What partnerships or strategic alliances could accelerate your growth?"
            ]

            if completed_count < len(fallback_questions):
                return fallback_questions[completed_count]
            else:
                return "What additional insights about your business model would be most valuable for potential investors?"

    def _get_question_focus_guidance(self, completed_count: int) -> str:
        """Provide focus guidance for question generation based on progress"""
        if completed_count < 3:
            return "Focus on Vision and core value proposition - understanding the fundamental purpose and impact"
        elif completed_count < 7:
            return "Focus on Market Opportunity & Growth - market size, timing, trends, and revenue model"
        elif completed_count < 10:
            return "Focus on USP & Competitive Advantage - differentiation, competitive landscape, and defensibility"
        elif completed_count < 14:
            return "Focus on Value Proposition - customer needs, solution fit, and market positioning"
        elif completed_count < 16:
            return "Focus on Sustainability - environmental, social, and economic long-term viability"
        else:
            return "Focus on Execution Feasibility - implementation challenges, resources, funding, and risks"

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
  "detailed_feedback": "<Insightful comments and qualitative evaluation from the AI reviewer. Provide comprehensive analysis highlighting key strengths, areas for improvement, and strategic recommendations.>",
  "evaluation_feedback": {{
    "Market Opportunity & Growth": {{
      "score": "x/5",
      "rationale": "<How large and fast-growing is the target market? Is this the right time to enter? Include TAM/SAM/SOM analysis, growth trends, timing, and revenue model assessment.>"
    }},
    "USP & Competitive Advantage": {{
      "score": "x/5",
      "rationale": "<What makes the idea unique? Can it stand out and stay ahead of competitors? Analyze differentiation, competitive landscape, barriers to entry, and defensibility.>"
    }},
    "Value Proposition": {{
      "score": "x/5",
      "rationale": "<What problem does it solve for users—and does it excite or inspire them? Evaluate customer pain points, solution fit, and emotional appeal.>"
    }},
    "Sustainability": {{
      "score": "x/5",
      "rationale": "<Is the business model environmentally, socially, and economically sustainable? Assess long-term viability and impact.>"
    }},
    "Execution Feasibility": {{
      "score": "x/5",
      "rationale": "<Can the idea be built and scaled with available resources and technology? Evaluate technical requirements, team capabilities, funding needs, and implementation risks.>"
    }},
    "overall": {{
      "score": "x/25",
      "feedback": "<Executive summary highlighting key strengths, critical risks, and strategic recommendations>"
    }}
  }},
  "paradigm_shift_drivers": [
    "<Key trend 1 influencing this business (e.g., generative AI, personalization, sustainability)>",
    "<Key trend 2 influencing this business (e.g., remote work, digital transformation)>",
    "<Key trend 3 influencing this business (e.g., social commerce, climate change)>"
  ],
  "benchmark_insights": "<Analysis of selected benchmark companies: What can we learn from their strategies? How do they validate market opportunity and competitive positioning?>",
  "spider_chart_business_opportunity": {{
    "Market Opportunity & Growth": "x/5",
    "USP & Competitive Advantage": "x/5",
    "Value Proposition": "x/5",
    "Sustainability": "x/5",
    "Execution Feasibility": "x/5",
    "total": "x/25"
  }},
  "investment_attractiveness": {{
    "Market Opportunity & Growth": {{
      "score": "x/5",
      "rationale": "<Analysis of market attractiveness, growth trajectory, and timing>"
    }},
    "USP & Competitive Advantage": {{
      "score": "x/5",
      "rationale": "<Evaluation of uniqueness and competitive defensibility>"
    }},
    "Execution Feasibility": {{
      "score": "x/5",
      "rationale": "<Assessment of implementation potential and resource availability>"
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
# AGENT ORCHESTRATOR - UPDATED WITH MANUAL LOGIC
# ===================================================================

class AgentOrchestrator:
    def __init__(self):
        self.state_manager = StateManagerAgent()
        self.consultant = BusinessConsultantAgent(self.state_manager)
        
    def process_user_input(self, user_input: str, conversation_id: str) -> str:
        try:
            print(f"\nORCHESTRATOR: Processing User Input")
            print(f"Input Length: {len(user_input)} characters")
            
            current_phase = st.session_state.business_state['phase']
            print(f"Current Phase: {current_phase}")
            
            # Handle different phases
            if current_phase == 'tic_collection':
                # Normal TIC collection with LLM
                response = client.responses.create(
                    model="gpt-4.1",
                    tools=self.consultant.tools,
                    instructions=self.consultant.system_instructions,
                    conversation=conversation_id,
                    input=[{"role": "user", "content": user_input}],
                    temperature=0.7
                )
                
                assistant_content = self._handle_agent_response(response, conversation_id)
                
                # Check if TIC 7 just completed - if so, auto-generate benchmark companies
                if (st.session_state.business_state['current_tic'] == 'completed' and 
                    st.session_state.business_state['phase'] == 'benchmarking'):
                    
                    print("TIC 7 COMPLETED - AUTO-GENERATING BENCHMARK COMPANIES")
                    benchmark_result = self._auto_generate_benchmark_companies()
                    if benchmark_result['success']:
                        assistant_content += "\n\nI've generated benchmark companies for your business idea. Please select 3 companies from the sidebar to proceed with detailed brainstorming."
                
                return assistant_content
                
            elif current_phase == 'benchmarking':
                # Check if 3 companies selected
                selected_companies = st.session_state.business_state.get('selected_companies', [])
                if len(selected_companies) == 3:
                    # Auto-start brainstorming
                    print("3 COMPANIES SELECTED - AUTO-STARTING BRAINSTORMING")
                    st.session_state.business_state['phase'] = 'brainstorming'
                    if 'brainstorming_progress' not in st.session_state.business_state:
                        st.session_state.business_state['brainstorming_progress'] = {
                            'current_question': 0,
                            'completed_count': 0,
                            'answers': {}
                        }
                    
                    # Generate first question based on TIC context and benchmark companies
                    first_question = self.consultant._generate_first_question()
                    # Store the question for validation and mark that first question is presented
                    st.session_state.business_state['brainstorming_progress']['current_question_text'] = first_question
                    st.session_state.business_state['brainstorming_progress']['first_question_presented'] = True
                    st.session_state.business_state['brainstorming_progress']['awaiting_first_answer'] = True
                    return f"Great! Now that you've selected your benchmark companies, let's dive deep into your business idea with detailed questions.\n\n**Question 1/20:** {first_question}"
                else:
                    # Use LLM for conversational response about company selection
                    response = client.responses.create(
                        model="gpt-4.1",
                        tools=[],
                        instructions="You are a business consultant. The user is in benchmarking phase and needs to select 3 companies from the sidebar. Be helpful and guide them to complete the selection. Keep response brief and conversational.",
                        conversation=conversation_id,
                        input=[{"role": "user", "content": user_input}],
                        temperature=0.7
                    )
                    return self._extract_assistant_content(response)
                    
            elif current_phase == 'brainstorming':
                # Handle brainstorming sequence automatically
                brainstorming_state = st.session_state.business_state.get('brainstorming_progress', {})
                completed_count = brainstorming_state.get('completed_count', 0)
                
                # Check if user is responding to the 10-question choice
                if completed_count == 10 and len(user_input.strip()) < 50:
                    user_choice = user_input.strip().lower()
                    if 'end' in user_choice or 'finish' in user_choice or 'stop' in user_choice:
                        print("USER CHOSE TO END BRAINSTORMING AT 10 QUESTIONS")
                        st.session_state.business_state['phase'] = 'evaluation_ready'
                        return "Perfect! You've completed the core brainstorming questions. You can now generate your comprehensive evaluation report from the sidebar."
                    elif 'continue' in user_choice or 'more' in user_choice or 'next' in user_choice:
                        print("USER CHOSE TO CONTINUE WITH REMAINING 10 QUESTIONS")
                        # Generate question 11 based on context
                        next_question = self.consultant._generate_next_question(10)
                        st.session_state.business_state['brainstorming_progress']['current_question_text'] = next_question
                        return f"**Question 11/20:** {next_question}"
                    else:
                        return "Please type 'end' to finish brainstorming or 'continue' to proceed with the remaining 10 questions."
                
                # Normal brainstorming sequence
                return self._handle_brainstorming_sequence(user_input)
            
            else:
                return "I'm ready to help you develop your business concept!"

        except Exception as e:
            error_msg = f"Error processing input: {str(e)}"
            print(f"ORCHESTRATOR ERROR: {error_msg}")
            return error_msg
    
    def _auto_generate_benchmark_companies(self) -> dict:
        """Automatically generate benchmark companies based on business idea"""
        try:
            # Get business context from TICs
            tic_progress = st.session_state.business_state['tic_progress']
            industry = st.session_state.business_state['industry']
            
            # Build context from completed TICs
            business_context = f"Industry: {industry}\n"
            for tic_name, tic_data in tic_progress.items():
                if tic_data['status'] == 'confirmed' and tic_data['summary']:
                    display_name = TIC_DISPLAY_NAMES.get(tic_name, tic_name)
                    business_context += f"{display_name}: {tic_data['summary']}\n"
            
            # Generate companies using OpenAI
            company_prompt = f"""Based on this business idea, suggest 5-6 real companies that would serve as good benchmarks for comparison and analysis.

Business Context:
{business_context}

Provide real companies that are:
1. Similar in business model or target market
2. Well-known and established
3. Relevant for competitive analysis
4. Mix of direct and indirect competitors

Return as JSON array with this format:
[
  {{
    "name": "Company Name",
    "description": "Brief description of what they do and why relevant",
    "relevance": "Why this is a good benchmark"
  }}
]

Return only valid JSON, no other text."""

            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": company_prompt}],
                response_format={"type": "json_object"},
                temperature=0.3
            )
            
            companies_data = json.loads(response.choices[0].message.content)
            
            # Handle both array and object responses
            if isinstance(companies_data, dict) and 'companies' in companies_data:
                companies = companies_data['companies']
            elif isinstance(companies_data, list):
                companies = companies_data
            else:
                companies = []
            
            # Call the actual tool
            result = self.consultant.handle_tool_call("generate_benchmark_companies", {
                "company_suggestions": companies
            })
            
            return result
            
        except Exception as e:
            print(f"ERROR AUTO-GENERATING BENCHMARK COMPANIES: {str(e)}")
            return {"success": False, "message": str(e)}

    def _handle_brainstorming_sequence(self, user_input: str) -> str:
        """Handle brainstorming phase with AI-generated adaptive questions"""
        try:
            brainstorming_state = st.session_state.business_state.get('brainstorming_progress', {})
            completed_count = brainstorming_state.get('completed_count', 0)
            current_question_text = brainstorming_state.get('current_question_text', '')

            print(f"BRAINSTORMING SEQUENCE START: Question {completed_count + 1}")
            print(f"User input: {user_input[:100]}...")

            # Check if user is giving a confirmation response instead of answering the actual question
            user_input_lower = user_input.strip().lower()
            confirmation_phrases = ['yes', 'ok', 'sure', "let's do it", "let's go", 'start', 'begin', 'ready']

            if (len(user_input.strip()) < 20 and
                any(phrase in user_input_lower for phrase in confirmation_phrases) and
                completed_count == 0):
                print("DETECTED CONFIRMATION RESPONSE - NOT A REAL ANSWER")
                return f"I can see you're ready to start! Please provide a detailed answer to the question above:\\n\\n**Question 1/20:** {current_question_text}"

            # Step 1: Validate if this is a meaningful answer using LLM
            validation_result = self._validate_brainstorming_answer(user_input, current_question_text, completed_count)

            if not validation_result['is_valid']:
                print(f"INVALID ANSWER DETECTED: {validation_result['reason']}")
                return validation_result['response']

            print("ANSWER VALIDATION PASSED")

            # Step 2: Store the Q&A pair and update progress
            print("STEP 2: Updating brainstorming progress...")

            # Store the current question and answer
            if 'answers' not in brainstorming_state:
                brainstorming_state['answers'] = {}

            brainstorming_state['answers'][completed_count] = {
                'question': current_question_text,
                'answer': user_input
            }

            # Determine question category based on progress
            question_category = self._get_question_category(completed_count)

            update_result = self.state_manager.handle_tool_call("update_brainstorming_progress", {
                "question_index": completed_count,
                "user_answer": user_input,
                "status": "completed",
                "question_text": current_question_text,
                "question_category": question_category
            })

            if not update_result['success']:
                return "Please provide a more detailed answer."

            print("STEP 2 COMPLETED: Brainstorming progress updated")

            # Step 3: Update TICs from brainstorming (dynamic mapping)
            print("STEP 3: Updating TICs from brainstorming...")
            self._update_tics_from_brainstorming(completed_count, user_input)
            print("STEP 3 COMPLETED: TICs updated from brainstorming")

            # Step 4: Check completion status and generate next question
            print("STEP 4: Getting next question...")
            updated_state = st.session_state.business_state['brainstorming_progress']
            new_completed_count = updated_state.get('completed_count', 0)

            print(f"New completed count: {new_completed_count}")

            # Check if we just completed 10 questions - offer choice
            if new_completed_count == 10:
                print("STEP 4: 10 QUESTIONS COMPLETED - OFFERING CHOICE")
                return "You've completed 10 out of 20 brainstorming questions! You can either:\n\n🚪 **End brainstorming here** and proceed to evaluation\n➡️ **Continue** with the remaining 10 questions\n\nWhat would you like to do? (Type 'end' to finish or 'continue' for more questions)"

            # Continue with remaining questions (up to 20)
            elif new_completed_count < 20:
                print(f"STEP 4: Generating next question ({new_completed_count + 1}/20)")
                next_question = self.consultant._generate_next_question(new_completed_count)

                # Store the generated question in state for validation
                st.session_state.business_state['brainstorming_progress']['current_question_text'] = next_question

                print(f"STEP 4 COMPLETED: Next question ready - Q{new_completed_count + 1}")
                return f"**Question {new_completed_count + 1}/20:** {next_question}"
            else:
                print("STEP 4 COMPLETED: All 20 questions finished")
                st.session_state.business_state['phase'] = 'evaluation_ready'
                return "Congratulations! You've completed all 20 brainstorming questions. You can now generate your evaluation report from the sidebar."

        except Exception as e:
            print(f"ERROR IN BRAINSTORMING SEQUENCE: {str(e)}")
            import traceback
            print(f"FULL ERROR TRACEBACK: {traceback.format_exc()}")
            return f"Error processing your answer: {str(e)}"
    
    def _validate_brainstorming_answer(self, user_input: str, question: str, question_index: int) -> dict:
        """Validate brainstorming answer using LLM for intelligent checking"""
        try:
            # Basic length check
            if len(user_input.strip()) < 10:
                return {
                    "is_valid": False,
                    "reason": "too_short",
                    "response": "Please provide a more detailed answer (at least a few words)."
                }
            
            # Use LLM to analyze if the response is a valid answer or needs help
            validation_prompt = f"""Analyze this user response to a brainstorming question.

Question: "{question}"
User Response: "{user_input}"

Determine if the user response is:

1. VALID_ANSWER: A meaningful attempt to answer the question (even if brief or needs more detail)
2. ASKING_QUESTION: User is asking for clarification or doesn't understand the question 
3. GIBBERISH: Random letters, nonsense, or completely unrelated content
4. NEEDS_HELP: User seems confused or stuck

For ASKING_QUESTION, provide a helpful explanation and rephrase the question.
For GIBBERISH or NEEDS_HELP, provide guidance to help them answer properly.

Respond in this exact JSON format:
{{
  "category": "VALID_ANSWER|ASKING_QUESTION|GIBBERISH|NEEDS_HELP",
  "explanation": "Brief explanation of why this category was chosen",
  "response": "What to say to the user (if not VALID_ANSWER)"
}}

Be helpful and encouraging. If user asks about terms like TAM, explain them clearly."""

            print("SENDING VALIDATION REQUEST TO LLM...")
            
            # Call OpenAI for validation
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": validation_prompt}],
                response_format={"type": "json_object"},
                temperature=0.3,
                max_tokens=300
            )
            
            validation_data = json.loads(response.choices[0].message.content)
            category = validation_data.get('category', 'NEEDS_HELP')
            
            print(f"VALIDATION RESULT: {category}")
            print(f"EXPLANATION: {validation_data.get('explanation', '')}")
            
            if category == 'VALID_ANSWER':
                return {
                    "is_valid": True,
                    "reason": "valid_answer",
                    "response": ""
                }
            else:
                # For all other categories, provide helpful response
                helpful_response = validation_data.get('response', 'Please provide a meaningful answer to the question.')
                return {
                    "is_valid": False,
                    "reason": category.lower(),
                    "response": helpful_response
                }
                
        except Exception as e:
            print(f"ERROR IN ANSWER VALIDATION: {str(e)}")
            # Fallback to basic validation
            if len(user_input.strip()) >= 15:
                return {"is_valid": True, "reason": "fallback_valid", "response": ""}
            else:
                return {
                    "is_valid": False, 
                    "reason": "fallback_short",
                    "response": "Please provide a more detailed answer."
                }
    
    def _update_tics_from_brainstorming(self, question_index: int, user_answer: str):
        """Update TICs based on brainstorming answer using AI analysis to better reflect user's evolving vision"""
        try:
            # Get the current question from brainstorming progress
            current_answers = st.session_state.business_state['brainstorming_progress']['answers']
            current_question = current_answers.get(str(question_index), {}).get('question', 'Unknown question')

            # Get all TIC summaries and current status for context
            tic_context = ""
            for tic_name in TIC_SEQUENCE:
                tic_data = st.session_state.business_state['tic_progress'][tic_name]
                if tic_data['summary']:
                    display_name = TIC_QUESTIONS[tic_name]['display_name']
                    tic_context += f"{display_name}: {tic_data['summary']}\n"

            # Use OpenAI to analyze how this answer relates to and updates the user's business vision
            analysis_prompt = f"""You are analyzing a user's brainstorming response to understand how it relates to and updates their business vision across different components.

CURRENT BUSINESS CONTEXT:
{tic_context}

BRAINSTORMING QUESTION: "{current_question}"
USER'S ANSWER: "{user_answer}"

Analyze this answer and determine:
1. Which TIC component(s) this most significantly relates to and enhances
2. What new insights about the user's vision this reveals
3. How this should update/enhance the existing summaries to better reflect their evolving vision

Available TIC categories:
- vision: Long-term goals, purpose, impact the business aims to achieve
- businessOverview: Core offering, what the business does, how it works
- marketSize: Market opportunity, size, growth trends, timing
- targetCustomers: Customer segments, demographics, characteristics
- valueProposition: Customer benefits, problems solved, value delivered
- usp: Unique advantages, differentiation, competitive positioning
- businessModel: Revenue streams, cost structure, sustainability, funding

Provide your response in this JSON format:
{
  "primary_tic": "tic_name",
  "secondary_tics": ["tic_name1", "tic_name2"],
  "vision_insights": "Key insights about user's evolving vision",
  "enhanced_summaries": {
    "tic_name": "Enhanced summary that better reflects user's vision"
  }
}"""

            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": analysis_prompt}],
                temperature=0.2,
                max_tokens=500
            )

            try:
                analysis = json.loads(response.choices[0].message.content.strip())

                # Update primary TIC
                primary_tic = analysis.get('primary_tic')
                if primary_tic and primary_tic in TIC_SEQUENCE:
                    enhanced_summaries = analysis.get('enhanced_summaries', {})
                    if primary_tic in enhanced_summaries:
                        current_tic_data = st.session_state.business_state['tic_progress'][primary_tic]
                        current_tic_data['summary'] = enhanced_summaries[primary_tic]
                        current_tic_data['enhanced_from_brainstorming'] = True
                        current_tic_data['vision_insights'] = analysis.get('vision_insights', '')

                # Update secondary TICs if provided
                secondary_tics = analysis.get('secondary_tics', [])
                enhanced_summaries = analysis.get('enhanced_summaries', {})
                for tic_name in secondary_tics:
                    if tic_name in TIC_SEQUENCE and tic_name in enhanced_summaries:
                        tic_data = st.session_state.business_state['tic_progress'][tic_name]
                        tic_data['summary'] = enhanced_summaries[tic_name]
                        tic_data['enhanced_from_brainstorming'] = True

                print(f"ENHANCED TICS FROM BRAINSTORMING: Primary={primary_tic}, Secondary={secondary_tics}")

            except json.JSONDecodeError:
                # Fallback: simple enhancement of most relevant TIC
                self._fallback_tic_enhancement(current_question, user_answer)

        except Exception as e:
            print(f"ERROR UPDATING TICS FROM BRAINSTORMING: {str(e)}")
            # Fallback enhancement
            self._fallback_tic_enhancement("Current question", user_answer)

    def _fallback_tic_enhancement(self, question: str, user_answer: str):
        """Simple fallback TIC enhancement when advanced analysis fails"""
        try:
            # Simple keyword-based mapping as fallback
            answer_lower = user_answer.lower()

            target_tic = "businessOverview"  # default
            if any(word in answer_lower for word in ['vision', 'goal', 'mission', 'purpose', 'impact']):
                target_tic = "vision"
            elif any(word in answer_lower for word in ['market', 'size', 'growth', 'opportunity']):
                target_tic = "marketSize"
            elif any(word in answer_lower for word in ['customer', 'user', 'segment', 'audience']):
                target_tic = "targetCustomers"
            elif any(word in answer_lower for word in ['value', 'benefit', 'solve', 'problem']):
                target_tic = "valueProposition"
            elif any(word in answer_lower for word in ['unique', 'different', 'advantage', 'compete']):
                target_tic = "usp"
            elif any(word in answer_lower for word in ['revenue', 'money', 'cost', 'price', 'business model']):
                target_tic = "businessModel"

            # Simple enhancement
            current_tic_data = st.session_state.business_state['tic_progress'][target_tic]
            current_summary = current_tic_data.get('summary', '')

            if current_summary:
                enhanced_summary = f"{current_summary} Additional insight: {user_answer[:100]}..."
            else:
                enhanced_summary = f"Based on brainstorming: {user_answer[:150]}..."

            current_tic_data['summary'] = enhanced_summary
            current_tic_data['enhanced_from_brainstorming'] = True

        except Exception as e:
            print(f"FALLBACK TIC ENHANCEMENT ERROR: {str(e)}")

    def _get_question_category(self, question_index: int) -> str:
        """Determine the category of a question based on its index and focus area"""
        focus_areas = {
            (0, 2): "Vision & Purpose",
            (3, 8): "Market Opportunity",
            (9, 11): "Competitive Advantage",
            (12, 15): "Value Proposition",
            (16, 16): "Sustainability",
            (17, 19): "Execution Feasibility"
        }

        for (start, end), category in focus_areas.items():
            if start <= question_index <= end:
                return category

        return "General Business"

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

st.title("Business Consultation System with Manual Tool Control")
st.markdown("*Automated TIC Collection → Auto Benchmarking → Auto Brainstorming → AI Evaluation*")

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
                        
                        # If 3 companies selected, auto-start brainstorming immediately
                        if len(st.session_state.business_state['selected_companies']) == 3:
                            print(f"3 COMPANIES SELECTED - AUTO-STARTING BRAINSTORMING IMMEDIATELY")
                            
                            # Change phase to brainstorming
                            st.session_state.business_state['phase'] = 'brainstorming'
                            
                            # Initialize brainstorming progress
                            if 'brainstorming_progress' not in st.session_state.business_state:
                                st.session_state.business_state['brainstorming_progress'] = {
                                    'current_question': 0,
                                    'completed_count': 0,
                                    'answers': {}
                                }
                            
                            # Generate the first question immediately
                            try:
                                orchestrator = st.session_state.orchestrator

                                # Ensure consultant has the method
                                if hasattr(orchestrator.consultant, '_generate_first_question'):
                                    first_question = orchestrator.consultant._generate_first_question()
                                else:
                                    # Fallback question if method doesn't exist
                                    first_question = "What specific problem does your business idea solve that existing solutions don't address effectively?"

                                # Store the question in brainstorming progress
                                st.session_state.business_state['brainstorming_progress']['current_question_text'] = first_question
                                # Create message with the first question
                                auto_message = f"Great! Now that you've selected your benchmark companies ({', '.join(st.session_state.business_state['selected_companies'])}), let's dive deep into your business idea with detailed questions.\n\n**Question 1/20:** {first_question}"

                                st.session_state.messages.append({"role": "assistant", "content": auto_message})
                            except Exception as e:
                                print(f"Error generating first question: {str(e)}")
                                # Fallback approach - just set a message without dynamic question
                                fallback_question = "What specific problem does your business idea solve that existing solutions don't address effectively?"
                                st.session_state.business_state['brainstorming_progress']['current_question_text'] = fallback_question
                                auto_message = f"Great! Now that you've selected your benchmark companies ({', '.join(st.session_state.business_state['selected_companies'])}), let's dive deep into your business idea with detailed questions.\n\n**Question 1/20:** {fallback_question}"
                                st.session_state.messages.append({"role": "assistant", "content": auto_message})

                            # Question is now generated and displayed immediately
                            
                            print(f"AUTO-STARTED BRAINSTORMING WITH FIRST QUESTION")
                    
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
            
            # Question Status - show dynamic questions that have been asked
            answers = brainstorming_state.get('answers', {})

            for i in range(20):  # Show up to 20 questions
                if str(i) in answers:
                    # Question has been asked and answered
                    q_data = answers[str(i)]
                    question_text = q_data.get('question', 'Dynamic question')
                    category = q_data.get('category', 'General')
                    question_short = question_text[:50] + "..." if len(question_text) > 50 else question_text
                    st.write(f"✅ {i+1}. [{category}] {question_short}")
                elif i == completed_questions:
                    # This is the current question being asked
                    st.write(f"🔄 {i+1}. [AI-Generated] Current question (dynamic)")
                    break  # Don't show future questions since they're dynamically generated
                else:
                    # Future questions not yet generated
                    if i < completed_questions + 3:  # Only show a few upcoming placeholders
                        st.write(f"⏳ {i+1}. [To be generated] AI will create this question based on your previous answers")
            
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

                # Overall Score & Investment Recommendation (Header)
                col1, col2 = st.columns(2)
                with col1:
                    overall_score = report['evaluation_feedback']['overall']['score']
                    st.metric("Overall Score", overall_score)

                with col2:
                    recommendation = report['ai_investment_recommendation']
                    rec_color = {
                        'YES': 'green',
                        'MAYBE': 'orange',
                        'NEUTRAL': 'gray',
                        'NO': 'red'
                    }.get(recommendation, 'gray')
                    st.markdown(f"**Investment Recommendation:** :{rec_color}[{recommendation}]")

                st.write(f"**Investment Rationale:** {report['investment_rationale']}")

                # Business Opportunity Evaluation (Top to Bottom View)
                st.subheader("📊 Business Opportunity Evaluation")

                # 1. Detailed Feedback
                with st.expander("💬 Detailed Feedback", expanded=True):
                    st.write(report.get('detailed_feedback', 'No detailed feedback available'))

                eval_feedback = report['evaluation_feedback']

                # 2. Market Opportunity & Growth
                with st.expander("📈 Market Opportunity & Growth", expanded=True):
                    st.metric("Score", eval_feedback['Market Opportunity & Growth']['score'])
                    st.write(eval_feedback['Market Opportunity & Growth']['rationale'])

                # 3. USP & Competitive Advantage
                with st.expander("🏆 USP & Competitive Advantage", expanded=True):
                    st.metric("Score", eval_feedback['USP & Competitive Advantage']['score'])
                    st.write(eval_feedback['USP & Competitive Advantage']['rationale'])

                # 4. Value Proposition
                with st.expander("🎯 Value Proposition", expanded=True):
                    st.metric("Score", eval_feedback['Value Proposition']['score'])
                    st.write(eval_feedback['Value Proposition']['rationale'])

                # 5. Sustainability
                with st.expander("🌱 Sustainability", expanded=True):
                    st.metric("Score", eval_feedback['Sustainability']['score'])
                    st.write(eval_feedback['Sustainability']['rationale'])

                # 6. Execution Feasibility
                with st.expander("⚙️ Execution Feasibility", expanded=True):
                    st.metric("Score", eval_feedback['Execution Feasibility']['score'])
                    st.write(eval_feedback['Execution Feasibility']['rationale'])

                # 7. Paradigm Shift Drivers
                with st.expander("🔮 Paradigm Shift Drivers", expanded=True):
                    if 'paradigm_shift_drivers' in report:
                        st.write("**Key trends and societal shifts influencing this business idea:**")
                        for i, driver in enumerate(report['paradigm_shift_drivers'], 1):
                            st.write(f"{i}. {driver}")
                    else:
                        st.write("Paradigm shift analysis not available")

                # 8. Benchmark Companies
                with st.expander("🏢 Benchmark Companies", expanded=True):
                    if 'benchmark_insights' in report:
                        st.write("**Leading companies and case studies used for reference:**")
                        for company in st.session_state.business_state['selected_companies']:
                            st.write(f"• {company}")
                        st.write("\n**Strategic Insights:**")
                        st.write(report['benchmark_insights'])
                    else:
                        st.write("**Selected Benchmark Companies:**")
                        for company in st.session_state.business_state['selected_companies']:
                            st.write(f"• {company}")

                # Investment Attractiveness Analysis
                with st.expander("💰 Investment Attractiveness Analysis"):
                    if 'investment_attractiveness' in report:
                        inv_data = report['investment_attractiveness']

                        col1, col2, col3 = st.columns(3)
                        with col1:
                            st.metric("Market Opportunity", inv_data['Market Opportunity & Growth']['score'])
                            st.caption(inv_data['Market Opportunity & Growth']['rationale'])

                        with col2:
                            st.metric("USP & Advantage", inv_data['USP & Competitive Advantage']['score'])
                            st.caption(inv_data['USP & Competitive Advantage']['rationale'])

                        with col3:
                            st.metric("Execution Feasibility", inv_data['Execution Feasibility']['score'])
                            st.caption(inv_data['Execution Feasibility']['rationale'])

                        st.metric("**Total Investment Score**", inv_data['total'])
                    else:
                        st.write("Investment attractiveness analysis not available")

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

        with st.spinner("Processing with automated tool control..."):
            print(f"\nUSER INPUT RECEIVED: {user_input}")
            print(f"Current Session: {st.session_state.current_session_id}")
            print(f"Current Business State: {st.session_state.business_state['phase']}")
            
            # Process through orchestrator with manual control
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
st.markdown("**Business Consultation System with Automated Tool Control** - Complete workflow automation!")
