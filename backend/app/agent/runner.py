import json
from datetime import timedelta

from openai import OpenAI, OpenAIError

from app.agent.prompt import SYSTEM_PROMPT
from app.agent.tools import definitions, execute_tool
from app.integrations.mock_valio import helsinki_today
from app.schemas import ChatOut, ProposalOut


def run_agent(settings, db, provider, actor, message):
    today = helsinki_today()
    monday = today + timedelta(days=(7 - today.weekday()) % 7 or 7)
    fallback = (
        "The AI assistant is unavailable. Use the guided request form below: it uses the same "
        f"catalog, offer checks and policy engine. Next Monday is {monday.isoformat()} (Europe/Helsinki). "
        "No order has been placed."
    )
    if not settings.deepseek_api_key:
        return ChatOut(message=fallback, mode="guided")
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT + f"\nHelsinki today: {today}; next Monday: {monday}."},
        {"role": "user", "content": message},
    ]
    results = []
    proposal = None
    proposed = False
    try:
        with OpenAI(
            api_key=settings.deepseek_api_key, base_url="https://api.deepseek.com", timeout=20, max_retries=0
        ) as client:
            for _step in range(6):
                completion = client.chat.completions.create(
                    model=settings.deepseek_model,
                    messages=messages,
                    tools=definitions(),
                    temperature=0,
                    max_tokens=1600,
                    extra_body={"thinking": {"type": "disabled"}},
                )
                choice = completion.choices[0].message
                if not choice.tool_calls:
                    return ChatOut(
                        message=choice.content or "Use the guided form to continue.",
                        mode="ai",
                        tool_results=results,
                        proposal=proposal,
                    )
                if len(choice.tool_calls) > 8:
                    break
                messages.append(
                    {
                        "role": "assistant",
                        "content": choice.content,
                        "tool_calls": [call.model_dump(exclude_none=True) for call in choice.tool_calls],
                    }
                )
                for call in choice.tool_calls:
                    if len(results) >= 20:
                        return ChatOut(
                            message="Tool budget reached. Continue in the guided form; no order was placed.",
                            mode="guided",
                            tool_results=results,
                            proposal=proposal,
                        )
                    if call.function.name == "create_order_proposal" and proposed:
                        result = {
                            "error": "ONE_PROPOSAL_PER_REQUEST",
                            "message": "Do not create duplicate proposals.",
                        }
                    else:
                        result = execute_tool(
                            call.function.name, call.function.arguments, db, provider, actor
                        )
                        if call.function.name == "create_order_proposal" and "error" not in result:
                            proposed = True
                            proposal = ProposalOut.model_validate(result)
                    results.append({"tool": call.function.name, "result": result})
                    messages.append({"role": "tool", "tool_call_id": call.id, "content": json.dumps(result)})
    except (OpenAIError, ValueError, IndexError):
        # Never return upstream exceptions: they can contain credentials or private response content.
        return ChatOut(message=fallback, mode="guided", tool_results=results, proposal=proposal)
    return ChatOut(
        message="Tool budget reached. Review verified results below; no order has been placed.",
        mode="guided",
        tool_results=results,
        proposal=proposal,
    )
