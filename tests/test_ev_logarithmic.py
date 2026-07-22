import unittest
from src.strategies.ev_logarithmic import solve_logarithmic_pure

class TestEvLogarithmic(unittest.TestCase):
    def test_solve_logarithmic_pure_fair_odds(self):
        # 50/50 odds
        odds = [2.0, 2.0]
        true_odds, true_probs = solve_logarithmic_pure(odds)
        
        # Fair odds should be 2.0, probs should be 0.5
        self.assertAlmostEqual(true_odds[0], 2.0, places=1)
        self.assertAlmostEqual(true_odds[1], 2.0, places=1)
        self.assertAlmostEqual(true_probs[0], 0.5, places=1)
        self.assertAlmostEqual(true_probs[1], 0.5, places=1)

    def test_solve_logarithmic_pure_3_outcomes(self):
        # Example 3-outcome odds (e.g., Football)
        odds = [2.0, 3.0, 4.0]
        true_odds, true_probs = solve_logarithmic_pure(odds)
        
        # Probs should sum to 1.0
        self.assertAlmostEqual(sum(true_probs), 1.0, places=5)

if __name__ == '__main__':
    unittest.main()
