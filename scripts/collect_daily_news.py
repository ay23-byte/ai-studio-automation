import argparse
from core.daily_news_pipeline import prepare_queue
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output-dir', default='output/daily_news')
    parser.add_argument('--state-dir', default='state/daily_news')
    args = parser.parse_args()
    path = prepare_queue(args.output_dir, args.state_dir)
    print('News discovery queue saved:', path)
    print('Candidate headlines are NOT verified stories or publishable scripts.')
if __name__ == '__main__': main()