import unittest

import numpy as np

from evaluate_board_lfw import compare_reference_embeddings


class BoardReferenceValidationTests(unittest.TestCase):
    def setUp(self):
        self.features = np.zeros((2, 128), dtype=np.int16)
        self.features[0, 3], self.features[1, 37] = 20000, -20000
        self.reference = self.features.astype(np.float64) / 20000
        self.valid = np.ones(2, dtype=bool)

    def test_matching_q15_features_pass_without_requiring_unit_integer_norm(self):
        result = compare_reference_embeddings(self.features, self.valid, self.reference, self.valid)
        self.assertTrue(result["passed"])
        self.assertEqual(result["images"], 2)

    def test_changed_feature_cannot_be_reported_as_a_successful_comparison(self):
        self.features[0, 3] = -20000
        result = compare_reference_embeddings(self.features, self.valid, self.reference, self.valid)
        self.assertFalse(result["passed"])
        self.assertEqual(result["images_below_required_cosine"], 1)

    def test_missing_board_or_reference_face_cannot_hide_in_the_common_subset(self):
        for missing_board in (False, True):
            with self.subTest(missing_board=missing_board):
                reduced = np.array([True, False])
                actual_valid, expected_valid = (reduced, self.valid) if missing_board else (self.valid, reduced)
                result = compare_reference_embeddings(self.features, actual_valid, self.reference, expected_valid)
                self.assertFalse(result["passed"])
                self.assertEqual(result["reference_valid_images_missing_on_board"], int(missing_board))
                self.assertEqual(result["board_valid_images_missing_in_reference"], int(not missing_board))

    def test_nonfinite_zero_and_no_common_features_are_rejected(self):
        for value in (float("nan"), float("inf"), 0):
            with self.subTest(value=value):
                reference = self.reference.copy()
                reference[0, 3] = value
                with self.assertRaises(RuntimeError):
                    compare_reference_embeddings(self.features, self.valid, reference, self.valid)
        with self.assertRaises(RuntimeError):
            compare_reference_embeddings(self.features, ~self.valid, self.reference, self.valid)

    def test_shape_mismatch_is_rejected_before_broadcasting(self):
        with self.assertRaises(ValueError):
            compare_reference_embeddings(self.features, self.valid, self.reference[:, :127], self.valid)


if __name__ == "__main__":
    unittest.main()
