from chessbuilder.tactical_benchmark import evaluate
from chessbuilder.tactics import LABELS

def test_annotated_tactical_regression_corpus():
    result=evaluate()
    assert not result["invalid"],result["invalid"]
    assert set(result["themes"])==set(LABELS)
    for theme,metrics in result["themes"].items():
        for kind,counts in metrics.items():
            assert counts["coverage"]>=2,(theme,kind,counts)
            assert counts["fp"]==0 and counts["fn"]==0,(theme,kind,counts)
