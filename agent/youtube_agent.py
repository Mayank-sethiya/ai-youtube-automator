import os
from crewai import Agent, Task, Crew, Process
from crewai_tools import YoutubeChannelSearchTool

# Get the API key from the environment
# IMPORTANT: You will need to set up your YOUTUBE_API_KEY for this to work
api_key = os.getenv("YOUTUBE_API_KEY")

# Check if the API key is available
if not api_key:
    # This is a temporary fallback for testing.
    # For production, you should raise an error or handle this case properly.
    print("Warning: YOUTUBE_API_KEY environment variable not set. Using a placeholder.")
    api_key = "YOUR_API_KEY_HERE" 

# Initialize the tool with the API key
youtube_tool = YoutubeChannelSearchTool(youtube_api_key=api_key)

# Define the researcher agent
researcher = Agent(
  role='YouTube Trend Analyst',
  goal='Identify trending topics and video ideas for a specific niche on YouTube',
  backstory="""You are an expert in analyzing YouTube trends.
  You use your skills to find out what's currently popular
  and suggest creative video ideas that have the potential to go viral.""",
  verbose=True,
  allow_delegation=False,
  tools=[youtube_tool]
)

# Define the task for the researcher
research_task = Task(
  description='Search for trending topics and video ideas in the AI and automation niche on YouTube.',
  expected_output='A list of 5-10 trending video ideas with a brief explanation for each.',
  agent=researcher
)

# Create the crew
youtube_crew = Crew(
  agents=[researcher],
  tasks=[research_task],
  process=Process.sequential
)
