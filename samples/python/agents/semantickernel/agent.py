import asyncio
import logging
import os
from enum import Enum

from collections.abc import AsyncIterable
from typing import TYPE_CHECKING, Annotated, Any, Literal

import httpx

from dotenv import load_dotenv
from pydantic import BaseModel
from semantic_kernel.agents import ChatCompletionAgent, ChatHistoryAgentThread
from semantic_kernel.connectors.ai.open_ai import (
    AzureChatCompletion,
    OpenAIChatCompletion,
    OpenAIChatPromptExecutionSettings,
)
from semantic_kernel.contents import (
    FunctionCallContent,
    FunctionResultContent,
    StreamingChatMessageContent,
    StreamingTextContent,
)
from semantic_kernel.functions import KernelArguments, kernel_function

if TYPE_CHECKING:
    from semantic_kernel.contents import ChatMessageContent
    from semantic_kernel.connectors.ai.chat_completion_client_base import ChatCompletionClientBase

logger = logging.getLogger(__name__)

load_dotenv()

# region Chat Service Configuration


class ChatServices(str, Enum):
    """Enum for supported chat completion services."""
    
    AZURE_OPENAI = "azure_openai"
    OPENAI = "openai"


service_id = "default"


def get_chat_completion_service(service_name: ChatServices) -> "ChatCompletionClientBase":
    """Return an appropriate chat completion service based on the service name.
    
    Args:
        service_name (ChatServices): Service name.
        
    Returns:
        ChatCompletionClientBase: Configured chat completion service.
        
    Raises:
        ValueError: If the service name is not supported or required environment variables are missing.
    """
    if service_name == ChatServices.AZURE_OPENAI:
        return _get_azure_openai_chat_completion_service()
    elif service_name == ChatServices.OPENAI:
        return _get_openai_chat_completion_service()
    else:
        raise ValueError(f"Unsupported service name: {service_name}")


def _get_azure_openai_chat_completion_service() -> AzureChatCompletion:
    """Return Azure OpenAI chat completion service.
    
    Returns:
        AzureChatCompletion: The configured Azure OpenAI service.
        
    Raises:
        ValueError: If required environment variables are not set.
    """
    required_env_vars = {
        'AZURE_OPENAI_ENDPOINT': os.getenv('AZURE_OPENAI_ENDPOINT'),
        'AZURE_OPENAI_API_KEY': os.getenv('AZURE_OPENAI_API_KEY'),
        'AZURE_OPENAI_CHAT_DEPLOYMENT_NAME': os.getenv('AZURE_OPENAI_CHAT_DEPLOYMENT_NAME'),
        'AZURE_OPENAI_API_VERSION': os.getenv('AZURE_OPENAI_API_VERSION')
    }
    
    missing_vars = [var_name for var_name, var_value in required_env_vars.items() if not var_value]
    if missing_vars:
        raise ValueError(
            f"Missing required Azure OpenAI environment variables: {', '.join(missing_vars)}"
        )
    
    return AzureChatCompletion(service_id=service_id)


def _get_openai_chat_completion_service() -> OpenAIChatCompletion:
    """Return OpenAI chat completion service.
    
    Returns:
        OpenAIChatCompletion: Configured OpenAI service.
        
    Raises:
        ValueError: If required environment variables are not set.
    """
    required_env_vars = {
        'OPENAI_API_KEY': os.getenv('OPENAI_API_KEY'),
        'OPENAI_MODEL_ID': os.getenv('OPENAI_MODEL_ID')
    }
    
    missing_vars = [var_name for var_name, var_value in required_env_vars.items() if not var_value]
    if missing_vars:
        raise ValueError(
            f"Missing required OpenAI environment variables: {', '.join(missing_vars)}"
        )
    
    return OpenAIChatCompletion(service_id=service_id)


def auto_detect_chat_service() -> "ChatCompletionClientBase":
    """Auto-detect and return an appropriate chat completion service based on available environment variables set in the .env file.
    
    Returns:
        ChatCompletionClientBase: The first available and properly configured chat service.
        
    Raises:
        ValueError: If no supported service configuration is found.
    """
    # Try Azure OpenAI first
    try:
        return get_chat_completion_service(ChatServices.AZURE_OPENAI)
    except ValueError:
        pass
    
    # Try OpenAI next
    try:
        return get_chat_completion_service(ChatServices.OPENAI)
    except ValueError:
        pass
    
    # If no chat completion service is available, raise an error
    raise ValueError(
        "No supported chat completion service configuration found. "
        "Please set either Azure OpenAI environment variables "
        "(AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY, AZURE_OPENAI_CHAT_DEPLOYMENT_NAME, AZURE_OPENAI_API_VERSION) "
        "or OpenAI environment variables (OPENAI_API_KEY, OPENAI_MODEL_ID)."
    )


# endregion

# region Plugin


class CurrencyPlugin:
    """A simple currency plugin that leverages Frankfurter for exchange rates.

    The Plugin is used by the `currency_exchange_agent`.
    """

    @kernel_function(
        description='Retrieves exchange rate between currency_from and currency_to using Frankfurter API'
    )
    def get_exchange_rate(
        self,
        currency_from: Annotated[
            str, 'Currency code to convert from, e.g. USD'
        ],
        currency_to: Annotated[
            str, 'Currency code to convert to, e.g. EUR or INR'
        ],
        date: Annotated[str, "Date or 'latest'"] = 'latest',
    ) -> str:
        try:
            response = httpx.get(
                f'https://api.frankfurter.app/{date}',
                params={'from': currency_from, 'to': currency_to},
                timeout=10.0,
            )
            response.raise_for_status()
            data = response.json()
            if 'rates' not in data or currency_to not in data['rates']:
                return f'Could not retrieve rate for {currency_from} to {currency_to}'
            rate = data['rates'][currency_to]
            return f'1 {currency_from} = {rate} {currency_to}'
        except Exception as e:
            return f'Currency API call failed: {e!s}'


# endregion

# region Response Format


class ResponseFormat(BaseModel):
    """A Response Format model to direct how the model should respond."""

    status: Literal['input_required', 'completed', 'error'] = 'input_required'
    message: str


# endregion

# region Semantic Kernel Agent


class SemanticKernelTravelAgent:
    """Wraps Semantic Kernel-based agents to handle Travel related tasks."""

    agent: ChatCompletionAgent
    thread: ChatHistoryAgentThread = None
    SUPPORTED_CONTENT_TYPES = ['text', 'text/plain']

    def __init__(self):
        # Auto-detect and configure the chat completion service
        # To explicitly specify a service, use: 
        # chat_service = get_chat_completion_service(ChatServices.AZURE_OPENAI)
        # or chat_service = get_chat_completion_service(ChatServices.OPENAI)
        chat_service = auto_detect_chat_service()
            
        currency_exchange_agent = ChatCompletionAgent(
            service=chat_service,
            name='CurrencyExchangeAgent',
            instructions=(
                'You specialize in handling currency-related requests from travelers. '
                'This includes providing current exchange rates, converting amounts between different currencies, '
                'explaining fees or charges related to currency exchange, and giving advice on the best practices for exchanging currency. '
                'Your goal is to assist travelers promptly and accurately with all currency-related questions.'
            ),
            plugins=[CurrencyPlugin()],
        )

        # Define an ActivityPlannerAgent to handle activity-related tasks
        activity_planner_agent = ChatCompletionAgent(
            service=chat_service,
            name='ActivityPlannerAgent',
            instructions=(
                'You specialize in planning and recommending activities for travelers. '
                'This includes suggesting sightseeing options, local events, dining recommendations, '
                'booking tickets for attractions, advising on travel itineraries, and ensuring activities '
                'align with traveler preferences and schedule. '
                'Your goal is to create enjoyable and personalized experiences for travelers.'
            ),
        )

        # Define the main TravelManagerAgent to delegate tasks to the appropriate agents
        self.agent = ChatCompletionAgent(
            service=chat_service,
            name='TravelManagerAgent',
            instructions=(
                "Your role is to carefully analyze the traveler's request and forward it to the appropriate agent based on the "
                'specific details of the query. '
                'Forward any requests involving monetary amounts, currency exchange rates, currency conversions, fees related '
                'to currency exchange, financial transactions, or payment methods to the CurrencyExchangeAgent. '
                'Forward requests related to planning activities, sightseeing recommendations, dining suggestions, event '
                'booking, itinerary creation, or any experiential aspects of travel that do not explicitly involve monetary '
                'transactions to the ActivityPlannerAgent. '
                'Your primary goal is precise and efficient delegation to ensure travelers receive accurate and specialized '
                'assistance promptly.'
            ),
            plugins=[currency_exchange_agent, activity_planner_agent],
            arguments=KernelArguments(
                settings=OpenAIChatPromptExecutionSettings(
                    response_format=ResponseFormat,
                )
            ),
        )

    async def invoke(self, user_input: str, session_id: str) -> dict[str, Any]:
        """Handle synchronous tasks (like tasks/send).

        Args:
            user_input (str): User input message.
            session_id (str): Unique identifier for the session.

        Returns:
            dict: A dictionary containing the content, task completion status,
            and user input requirement.
        """
        await self._ensure_thread_exists(session_id)

        # Use SK's get_response for a single shot
        response = await self.agent.get_response(
            messages=user_input,
            thread=self.thread,
        )
        return self._get_agent_response(response.content)

    async def stream(
        self,
        user_input: str,
        session_id: str,
    ) -> AsyncIterable[dict[str, Any]]:
        """For streaming tasks we yield the SK agent's invoke_stream progress.

        Args:
            user_input (str): User input message.
            session_id (str): Unique identifier for the session.

        Yields:
            dict: A dictionary containing the content, task completion status,
            and user input requirement.
        """
        await self._ensure_thread_exists(session_id)

        plugin_notice_seen = False
        plugin_event = asyncio.Event()

        text_notice_seen = False
        chunks: list[StreamingChatMessageContent] = []

        async def _handle_intermediate_message(
            message: 'ChatMessageContent',
        ) -> None:
            """Handle intermediate messages from the agent."""
            nonlocal plugin_notice_seen
            if not plugin_notice_seen:
                plugin_notice_seen = True
                plugin_event.set()
            # An example of handling intermediate messages during function calling
            for item in message.items or []:
                if isinstance(item, FunctionResultContent):
                    print(
                        f'SK Function Result:> {item.result} for function: {item.name}'
                    )
                elif isinstance(item, FunctionCallContent):
                    print(
                        f'SK Function Call:> {item.name} with arguments: {item.arguments}'
                    )
                else:
                    print(f'SK Message:> {item}')

        async for chunk in self.agent.invoke_stream(
            messages=user_input,
            thread=self.thread,
            on_intermediate_message=_handle_intermediate_message,
        ):
            if plugin_event.is_set():
                yield {
                    'is_task_complete': False,
                    'require_user_input': False,
                    'content': 'Processing function calls...',
                }
                plugin_event.clear()

            if any(isinstance(i, StreamingTextContent) for i in chunk.items):
                if not text_notice_seen:
                    yield {
                        'is_task_complete': False,
                        'require_user_input': False,
                        'content': 'Building the output...',
                    }
                    text_notice_seen = True
                chunks.append(chunk.message)

        if chunks:
            yield self._get_agent_response(sum(chunks[1:], chunks[0]))

    def _get_agent_response(
        self, message: 'ChatMessageContent'
    ) -> dict[str, Any]:
        """Extracts the structured response from the agent's message content.

        Args:
            message (ChatMessageContent): The message content from the agent.

        Returns:
            dict: A dictionary containing the content, task completion status, and user input requirement.
        """
        structured_response = ResponseFormat.model_validate_json(
            message.content
        )

        default_response = {
            'is_task_complete': False,
            'require_user_input': True,
            'content': 'We are unable to process your request at the moment. Please try again.',
        }

        if isinstance(structured_response, ResponseFormat):
            response_map = {
                'input_required': {
                    'is_task_complete': False,
                    'require_user_input': True,
                },
                'error': {
                    'is_task_complete': False,
                    'require_user_input': True,
                },
                'completed': {
                    'is_task_complete': True,
                    'require_user_input': False,
                },
            }

            response = response_map.get(structured_response.status)
            if response:
                return {**response, 'content': structured_response.message}

        return default_response

    async def _ensure_thread_exists(self, session_id: str) -> None:
        """Ensure the thread exists for the given session ID.

        Args:
            session_id (str): Unique identifier for the session.
        """
        if self.thread is None or self.thread.id != session_id:
            await self.thread.delete() if self.thread else None
            self.thread = ChatHistoryAgentThread(thread_id=session_id)


# endregion