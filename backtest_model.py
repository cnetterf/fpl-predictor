import argparse

import server


def parse_args():
    parser = argparse.ArgumentParser(
        description="Backtest Official FPL and Elo Insights predictions against finished gameweeks."
    )
    parser.add_argument(
        "--recompute",
        action="store_true",
        help="Attempt a source-data refresh before recomputing backtests.",
    )
    return parser.parse_args()


def print_summary(dataset):
    print("Source      Windows  AvgPlayers  AvgMAE  AvgRMSE  AvgTop20Overlap  AvgNDCG20  AvgSpearman")
    for source, summary in dataset.get("overview", {}).items():
        print(
            f"{source:<11} {summary['windows']:<7} {summary['avg_players']:<11.2f} "
            f"{summary['avg_mae']:<7.3f} {summary['avg_rmse']:<8.3f} "
            f"{summary['avg_top20_overlap']:<16.2f} {summary['avg_ndcg20']:<10.4f} "
            f"{summary['avg_spearman']:.4f}"
        )


def main():
    args = parse_args()
    dataset = server.APP.get_backtest_dataset(recompute=args.recompute)
    print_summary(dataset)
    print()
    print("Notes:")
    for note in dataset.get("notes", []):
        print(f"- {note}")


if __name__ == "__main__":
    main()
