from agent.youtube_agent import youtube_crew

def run():
    """
    This function kicks off the crew to start the process.
    """
    # You can provide inputs to the crew here
    inputs = {
        # 'topic': 'Your specific topic' # Example of how to provide input
    }
    result = youtube_crew.kickoff(inputs=inputs)
    print(result)

if __name__ == "__main__":
    run()