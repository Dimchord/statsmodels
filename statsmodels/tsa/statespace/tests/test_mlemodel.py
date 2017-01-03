"""
Tests for the generic MLEModel

Author: Chad Fulton
License: Simplified-BSD
"""
from __future__ import division, absolute_import, print_function

import numpy as np
import pandas as pd
import os
import re

import warnings
from statsmodels.tsa.statespace import sarimax, kalman_filter, kalman_smoother
from statsmodels.tsa.statespace.mlemodel import MLEModel, MLEResultsWrapper
from statsmodels.tsa.statespace.tools import compatibility_mode
from statsmodels.datasets import nile
from numpy.testing import assert_almost_equal, assert_equal, assert_allclose, assert_raises
from nose.exc import SkipTest
from statsmodels.tsa.statespace.tests.results import results_sarimax

current_path = os.path.dirname(os.path.abspath(__file__))

try:
    import matplotlib.pyplot as plt
    have_matplotlib = True
except ImportError:
    have_matplotlib = False

# Basic kwargs
kwargs = {
    'k_states': 1, 'design': [[1]], 'transition': [[1]],
    'selection': [[1]], 'state_cov': [[1]],
    'initialization': 'approximate_diffuse'
}


def get_dummy_mod(fit=True, pandas=False):
    # This tests time-varying parameters regression when in fact the parameters
    # are not time-varying, and in fact the regression fit is perfect
    endog = np.arange(100)*1.0
    exog = 2*endog

    if pandas:
        index = pd.date_range('1960-01-01', periods=100, freq='MS')
        endog = pd.Series(endog, index=index)
        exog = pd.Series(exog, index=index)

    mod = sarimax.SARIMAX(endog, exog=exog, order=(0,0,0), time_varying_regression=True, mle_regression=False)

    if fit:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            res = mod.fit(disp=-1)
    else:
        res = None
    
    return mod, res


def test_wrapping():
    # Test the wrapping of various Representation / KalmanFilter /
    # KalmanSmoother methods / attributes
    mod, _ = get_dummy_mod(fit=False)

    # Test that we can get the design matrix
    assert_equal(mod['design', 0, 0], 2.0 * np.arange(100))

    # Test that we can set individual elements of the design matrix
    mod['design', 0, 0, :] = 2
    assert_equal(mod.ssm['design', 0, 0, :], 2)
    assert_equal(mod.ssm['design'].shape, (1, 1, 100))

    # Test that we can set the entire design matrix
    mod['design'] = [[3.]]
    assert_equal(mod.ssm['design', 0, 0], 3.)
    # (Now it's no longer time-varying, so only 2-dim)
    assert_equal(mod.ssm['design'].shape, (1, 1))

    # Test that we can change the following properties: loglikelihood_burn,
    # initial_variance, tolerance
    assert_equal(mod.loglikelihood_burn, 1)
    mod.loglikelihood_burn = 0
    assert_equal(mod.ssm.loglikelihood_burn, 0)

    assert_equal(mod.tolerance, mod.ssm.tolerance)
    mod.tolerance = 0.123
    assert_equal(mod.ssm.tolerance, 0.123)

    assert_equal(mod.initial_variance, 1e10)
    mod.initial_variance = 1e12
    assert_equal(mod.ssm.initial_variance, 1e12)

    # Test that we can use the following wrappers: initialization,
    # initialize_known, initialize_stationary, initialize_approximate_diffuse

    # Initialization starts off as none
    assert_equal(mod.initialization, None)

    # Since the SARIMAX model may be fully stationary or may have diffuse
    # elements, it uses a custom initialization by default, but it can be
    # overridden by users
    mod.initialize_state()
    # (The default initialization in this case is known because there is a non-
    # stationary state corresponding to the time-varying regression parameter)
    assert_equal(mod.initialization, 'known')

    mod.initialize_approximate_diffuse(1e5)
    assert_equal(mod.initialization, 'approximate_diffuse')
    assert_equal(mod.ssm._initial_variance, 1e5)

    mod.initialize_known([5.], [[40]])
    assert_equal(mod.initialization, 'known')
    assert_equal(mod.ssm._initial_state, [5.])
    assert_equal(mod.ssm._initial_state_cov, [[40]])

    mod.initialize_stationary()
    assert_equal(mod.initialization, 'stationary')

    # Test that we can use the following wrapper methods: set_filter_method,
    # set_stability_method, set_conserve_memory, set_smoother_output

    # The defaults are as follows:
    assert_equal(mod.ssm.filter_method, kalman_filter.FILTER_CONVENTIONAL)
    assert_equal(mod.ssm.stability_method, kalman_filter.STABILITY_FORCE_SYMMETRY)
    assert_equal(mod.ssm.conserve_memory, kalman_filter.MEMORY_STORE_ALL)
    assert_equal(mod.ssm.smoother_output, kalman_smoother.SMOOTHER_ALL)

    # Now, create the Cython filter object and assert that they have
    # transferred correctly
    mod.ssm._initialize_filter()
    kf = mod.ssm._kalman_filter
    assert_equal(kf.filter_method, kalman_filter.FILTER_CONVENTIONAL)
    assert_equal(kf.stability_method, kalman_filter.STABILITY_FORCE_SYMMETRY)
    assert_equal(kf.conserve_memory, kalman_filter.MEMORY_STORE_ALL)
    # (the smoother object is so far not in Cython, so there is no
    # transferring)

    # Change the attributes in the model class
    if compatibility_mode:
        assert_raises(NotImplementedError, mod.set_filter_method, 100)
    else:
        mod.set_filter_method(100)
    mod.set_stability_method(101)
    mod.set_conserve_memory(102)
    mod.set_smoother_output(103)

    # Assert that the changes have occurred in the ssm class
    if not compatibility_mode:
        assert_equal(mod.ssm.filter_method, 100)
    assert_equal(mod.ssm.stability_method, 101)
    assert_equal(mod.ssm.conserve_memory, 102)
    assert_equal(mod.ssm.smoother_output, 103)

    # Assert that the changes have *not yet* occurred in the filter object
    assert_equal(kf.filter_method, kalman_filter.FILTER_CONVENTIONAL)
    assert_equal(kf.stability_method, kalman_filter.STABILITY_FORCE_SYMMETRY)
    assert_equal(kf.conserve_memory, kalman_filter.MEMORY_STORE_ALL)

    # Re-initialize the filter object (this would happen automatically anytime
    # loglike, filter, etc. were called)
    # In this case, an error will be raised since filter_method=100 is not
    # valid
    # Note: this error is only raised in the compatibility case, since the
    # newer filter logic checks for a valid filter mode at a different point
    if compatibility_mode:
        assert_raises(NotImplementedError, mod.ssm._initialize_filter)

    # Now, test the setting of the other two methods by resetting the
    # filter method to a valid value
    mod.set_filter_method(1)
    mod.ssm._initialize_filter()
    # Retrieve the new kalman filter object (a new object had to be created
    # due to the changing filter method)
    kf = mod.ssm._kalman_filter

    assert_equal(kf.filter_method, 1)
    assert_equal(kf.stability_method, 101)
    assert_equal(kf.conserve_memory, 102)


def test_fit_misc():
    true = results_sarimax.wpi1_stationary
    endog = np.diff(true['data'])[1:]

    mod = sarimax.SARIMAX(endog, order=(1,0,1), trend='c')

    # Test optim_hessian={'opg','oim','approx'}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        res1 = mod.fit(method='ncg', disp=0, optim_hessian='opg', optim_complex_step=False)
        res2 = mod.fit(method='ncg', disp=0, optim_hessian='oim', optim_complex_step=False)
    # Check that the Hessians broadly result in the same optimum
    assert_allclose(res1.llf, res2.llf, rtol=1e-2)

    # Test return_params=True
    mod, _ = get_dummy_mod(fit=False)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        res_params = mod.fit(disp=-1, return_params=True)

    # 5 digits necessary to accommodate 32-bit numpy / scipy with OpenBLAS 0.2.18
    assert_almost_equal(res_params, [0, 0], 5)


def test_score_misc():
    mod, res = get_dummy_mod()

    # Test that the score function works
    mod.score(res.params)


def test_from_formula():
    assert_raises(NotImplementedError, lambda: MLEModel.from_formula(1,2,3))


def test_score_analytic_ar1():
    # Test the score against the analytic score for an AR(1) model with 2
    # observations
    # Let endog = [1, 0.5], params=[0, 1]
    mod = sarimax.SARIMAX([1, 0.5], order=(1,0,0))

    def partial_phi(phi, sigma2):
        return -0.5 * (phi**2 + 2*phi*sigma2 - 1) / (sigma2 * (1 - phi**2))

    def partial_sigma2(phi, sigma2):
        return -0.5 * (2*sigma2 + phi - 1.25) / (sigma2**2)

    params = np.r_[0., 2]

    # Compute the analytic score
    analytic_score = np.r_[
        partial_phi(params[0], params[1]),
        partial_sigma2(params[0], params[1])]

    # Check each of the approximations, transformed parameters
    approx_cs = mod.score(params, transformed=True, approx_complex_step=True)
    assert_allclose(approx_cs, analytic_score)

    approx_fd = mod.score(params, transformed=True, approx_complex_step=False)
    assert_allclose(approx_fd, analytic_score, atol=1e-5)

    approx_fd_centered = (
        mod.score(params, transformed=True, approx_complex_step=False,
                  approx_centered=True))
    assert_allclose(approx_fd, analytic_score, atol=1e-5)

    harvey_cs = mod.score(params, transformed=True, method='harvey',
                          approx_complex_step=True)
    assert_allclose(harvey_cs, analytic_score)
    harvey_fd = mod.score(params, transformed=True, method='harvey',
                          approx_complex_step=False)
    assert_allclose(harvey_fd, analytic_score, atol=1e-5)
    harvey_fd_centered = mod.score(params, transformed=True, method='harvey',
                                   approx_complex_step=False,
                                   approx_centered=True)
    assert_allclose(harvey_fd_centered, analytic_score, atol=1e-5)

    # Check the approximations for untransformed parameters. The analytic
    # check now comes from chain rule with the analytic derivative of the
    # transformation
    # if L* is the likelihood evaluated at untransformed parameters and
    # L is the likelihood evaluated at transformed parameters, then we have:
    # L*(u) = L(t(u))
    # and then
    # L'*(u) = L'(t(u)) * t'(u)
    def partial_transform_phi(phi):
        return -1. / (1 + phi**2)**(3./2)

    def partial_transform_sigma2(sigma2):
        return 2. * sigma2

    uparams = mod.untransform_params(params)

    analytic_score = np.dot(
        np.diag(np.r_[partial_transform_phi(uparams[0]),
                      partial_transform_sigma2(uparams[1])]),
        np.r_[partial_phi(params[0], params[1]),
              partial_sigma2(params[0], params[1])])

    approx_cs = mod.score(uparams, transformed=False, approx_complex_step=True)
    assert_allclose(approx_cs, analytic_score)

    approx_fd = mod.score(uparams, transformed=False,
                          approx_complex_step=False)
    assert_allclose(approx_fd, analytic_score, atol=1e-5)

    approx_fd_centered = (
        mod.score(uparams, transformed=False, approx_complex_step=False,
                  approx_centered=True))
    assert_allclose(approx_fd, analytic_score, atol=1e-5)

    harvey_cs = mod.score(uparams, transformed=False, method='harvey',
                          approx_complex_step=True)
    assert_allclose(harvey_cs, analytic_score)
    harvey_fd = mod.score(uparams, transformed=False, method='harvey',
                          approx_complex_step=False)
    assert_allclose(harvey_fd, analytic_score, atol=1e-5)
    harvey_fd_centered = mod.score(uparams, transformed=False, method='harvey',
                                   approx_complex_step=False,
                                   approx_centered=True)
    assert_allclose(harvey_fd_centered, analytic_score, atol=1e-5)

    # Check the Hessian: these approximations are not very good, particularly
    # when phi is close to 0
    params = np.r_[0.5, 1.]
    def hessian(phi, sigma2):
        hessian = np.zeros((2,2))
        hessian[0,0] = (-phi**2 - 1) / (phi**2 - 1)**2
        hessian[1,0] = hessian[0,1] = -1 / (2 * sigma2**2)
        hessian[1,1] = (sigma2 + phi - 1.25) / sigma2**3
        return hessian

    analytic_hessian = hessian(params[0], params[1])

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        assert_allclose(mod._hessian_complex_step(params) * 2,
                        analytic_hessian, atol=1e-1)
        assert_allclose(mod._hessian_finite_difference(params) * 2,
                        analytic_hessian, atol=1e-1)


def test_cov_params():
    mod, res = get_dummy_mod()

    # Smoke test for each of the covariance types
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        res = mod.fit(res.params, disp=-1, cov_type='none')
        assert_equal(res.cov_kwds['description'], 'Covariance matrix not calculated.')
        res = mod.fit(res.params, disp=-1, cov_type='approx')
        assert_equal(res.cov_type, 'approx')
        assert_equal(res.cov_kwds['description'], 'Covariance matrix calculated using numerical (complex-step) differentiation.')
        res = mod.fit(res.params, disp=-1, cov_type='oim')
        assert_equal(res.cov_type, 'oim')
        assert_equal(res.cov_kwds['description'], 'Covariance matrix calculated using the observed information matrix (complex-step) described in Harvey (1989).')
        res = mod.fit(res.params, disp=-1, cov_type='opg')
        assert_equal(res.cov_type, 'opg')
        assert_equal(res.cov_kwds['description'], 'Covariance matrix calculated using the outer product of gradients (complex-step).')
        res = mod.fit(res.params, disp=-1, cov_type='robust')
        assert_equal(res.cov_type, 'robust')
        assert_equal(res.cov_kwds['description'], 'Quasi-maximum likelihood covariance matrix used for robustness to some misspecifications; calculated using the observed information matrix (complex-step) described in Harvey (1989).')
        res = mod.fit(res.params, disp=-1, cov_type='robust_oim')
        assert_equal(res.cov_type, 'robust_oim')
        assert_equal(res.cov_kwds['description'], 'Quasi-maximum likelihood covariance matrix used for robustness to some misspecifications; calculated using the observed information matrix (complex-step) described in Harvey (1989).')
        res = mod.fit(res.params, disp=-1, cov_type='robust_approx')
        assert_equal(res.cov_type, 'robust_approx')
        assert_equal(res.cov_kwds['description'], 'Quasi-maximum likelihood covariance matrix used for robustness to some misspecifications; calculated using numerical (complex-step) differentiation.')
        assert_raises(NotImplementedError, mod.fit, res.params, disp=-1, cov_type='invalid_cov_type')


def test_transform():
    # The transforms in MLEModel are noops
    mod = MLEModel([1,2], **kwargs)

    # Test direct transform, untransform
    assert_allclose(mod.transform_params([2, 3]), [2, 3])
    assert_allclose(mod.untransform_params([2, 3]), [2, 3])    

    # Smoke test for transformation in `filter`, `update`, `loglike`,
    # `loglikeobs`
    mod.filter([], transformed=False)
    mod.update([], transformed=False)
    mod.loglike([], transformed=False)
    mod.loglikeobs([], transformed=False)

    # Note that mod is an SARIMAX instance, and the two parameters are
    # variances
    mod, _ = get_dummy_mod(fit=False)

    # Test direct transform, untransform
    assert_allclose(mod.transform_params([2, 3]), [4, 9])
    assert_allclose(mod.untransform_params([4, 9]), [2, 3])

    # Test transformation in `filter`
    res = mod.filter([2, 3], transformed=True)
    assert_allclose(res.params, [2, 3])

    res = mod.filter([2, 3], transformed=False)
    assert_allclose(res.params, [4, 9])


def test_filter():
    endog = np.array([1., 2.])
    mod = MLEModel(endog, **kwargs)

    # Test return of ssm object
    res = mod.filter([], return_ssm=True)
    assert_equal(isinstance(res, kalman_filter.FilterResults), True)

    # Test return of full results object
    res = mod.filter([])
    assert_equal(isinstance(res, MLEResultsWrapper), True)
    assert_equal(res.cov_type, 'opg')

    # Test return of full results object, specific covariance type
    res = mod.filter([], cov_type='oim')
    assert_equal(isinstance(res, MLEResultsWrapper), True)
    assert_equal(res.cov_type, 'oim')


def test_params():
    mod = MLEModel([1,2], **kwargs)

    # By default start_params raises NotImplementedError
    assert_raises(NotImplementedError, lambda: mod.start_params)
    # But param names are by default an empty array
    assert_equal(mod.param_names, [])

    # We can set them in the object if we want
    mod._start_params = [1]
    mod._param_names = ['a']

    assert_equal(mod.start_params, [1])
    assert_equal(mod.param_names, ['a'])


def check_results(pandas):
    mod, res = get_dummy_mod(pandas=pandas)

    # Test fitted values
    assert_almost_equal(res.fittedvalues[2:], mod.endog[2:].squeeze())

    # Test residuals
    assert_almost_equal(res.resid[2:], np.zeros(mod.nobs-2))

    # Test loglikelihood_burn
    assert_equal(res.loglikelihood_burn, 1)


def test_results(pandas=False):
    check_results(pandas=False)
    check_results(pandas=True)


def test_predict():
    dates = pd.date_range(start='1980-01-01', end='1981-01-01', freq='AS')
    endog = pd.Series([1,2], index=dates)
    mod = MLEModel(endog, **kwargs)
    res = mod.filter([])

    # Test that predict with start=None, end=None does prediction with full
    # dataset
    predict = res.predict()
    assert_equal(predict.shape, (mod.nobs,))
    assert_allclose(res.get_prediction().predicted_mean, predict)

    # Test a string value to the dynamic option
    assert_allclose(res.predict(dynamic='1981-01-01'), res.predict())

    # Test an invalid date string value to the dynamic option
    # assert_raises(ValueError, res.predict, dynamic='1982-01-01')

    # Test for passing a string to predict when dates are not set
    mod = MLEModel([1,2], **kwargs)
    res = mod.filter([])
    assert_raises(KeyError, res.predict, dynamic='string')


def test_forecast():
    # Numpy
    mod = MLEModel([1,2], **kwargs)
    res = mod.filter([])
    forecast = res.forecast(steps=10)
    assert_allclose(forecast, np.ones((10,)) * 2)
    assert_allclose(res.get_forecast(steps=10).predicted_mean, forecast)

    # Pandas
    index = pd.date_range('1960-01-01', periods=2, freq='MS')
    mod = MLEModel(pd.Series([1,2], index=index), **kwargs)
    res = mod.filter([])
    assert_allclose(res.forecast(steps=10), np.ones((10,)) * 2)
    assert_allclose(res.forecast(steps='1960-12-01'), np.ones((10,)) * 2)
    assert_allclose(res.get_forecast(steps=10).predicted_mean, np.ones((10,)) * 2)


def test_summary():
    dates = pd.date_range(start='1980-01-01', end='1984-01-01', freq='AS')
    endog = pd.Series([1,2,3,4,5], index=dates)
    mod = MLEModel(endog, **kwargs)
    res = mod.filter([])

    # Get the summary
    txt = str(res.summary())

    # Test res.summary when the model has dates
    assert_equal(re.search('Sample:\s+01-01-1980', txt) is not None, True)
    assert_equal(re.search('\s+- 01-01-1984', txt) is not None, True)

    # Test res.summary when `model_name` was not provided
    assert_equal(re.search('Model:\s+MLEModel', txt) is not None, True)

    # Smoke test that summary still works when diagnostic tests fail
    res.filter_results._standardized_forecasts_error[:] = np.nan
    res.summary()
    res.filter_results._standardized_forecasts_error = 1
    res.summary()
    res.filter_results._standardized_forecasts_error = 'a'
    res.summary()


def check_endog(endog, nobs=2, k_endog=1, **kwargs):
    # create the model
    mod = MLEModel(endog, **kwargs)
    # the data directly available in the model is the Statsmodels version of
    # the data; it should be 2-dim, C-contiguous, long-shaped:
    # (nobs, k_endog) == (2, 1)
    assert_equal(mod.endog.ndim, 2)
    assert_equal(mod.endog.flags['C_CONTIGUOUS'], True)
    assert_equal(mod.endog.shape, (nobs, k_endog))
    # the data in the `ssm` object is the state space version of the data; it
    # should be 2-dim, F-contiguous, wide-shaped (k_endog, nobs) == (1, 2)
    # and it should share data with mod.endog
    assert_equal(mod.ssm.endog.ndim, 2)
    assert_equal(mod.ssm.endog.flags['F_CONTIGUOUS'], True)
    assert_equal(mod.ssm.endog.shape, (k_endog, nobs))
    assert_equal(mod.ssm.endog.base is mod.endog, True)

    return mod

def test_basic_endog():
    # Test various types of basic python endog inputs (e.g. lists, scalars...)

    # Check cannot call with non-array-like
    # fails due to checks in Statsmodels base classes
    assert_raises(ValueError, MLEModel, endog=1, k_states=1)
    assert_raises(ValueError, MLEModel, endog='a', k_states=1)
    assert_raises(ValueError, MLEModel, endog=True, k_states=1)

    # Check behavior with different types
    mod = MLEModel([1], **kwargs)
    res = mod.filter([])
    assert_equal(res.filter_results.endog, [[1]])

    mod = MLEModel([1.], **kwargs)
    res = mod.filter([])
    assert_equal(res.filter_results.endog, [[1]])

    mod = MLEModel([True], **kwargs)
    res = mod.filter([])
    assert_equal(res.filter_results.endog, [[1]])

    mod = MLEModel(['a'], **kwargs)
    # raises error due to inability coerce string to numeric
    assert_raises(ValueError, mod.filter, [])

    # Check that a different iterable tpyes give the expected result
    endog = [1.,2.]
    mod = check_endog(endog, **kwargs)
    mod.filter([])

    endog = [[1.],[2.]]
    mod = check_endog(endog, **kwargs)
    mod.filter([])

    endog = (1.,2.)
    mod = check_endog(endog, **kwargs)
    mod.filter([])

def test_numpy_endog():
    # Test various types of numpy endog inputs

    # Check behavior of the link maintained between passed `endog` and
    # `mod.endog` arrays
    endog = np.array([1., 2.])
    mod = MLEModel(endog, **kwargs)
    assert_equal(mod.endog.base is not mod.data.orig_endog, True)
    assert_equal(mod.endog.base is not endog, True)
    assert_equal(mod.data.orig_endog.base is not endog, True)
    endog[0] = 2
    # there is no link to mod.endog
    assert_equal(mod.endog, np.r_[1, 2].reshape(2,1))
    # there remains a link to mod.data.orig_endog
    assert_equal(mod.data.orig_endog, endog)

    # Check behavior with different memory layouts / shapes

    # Example  (failure): 0-dim array
    endog = np.array(1.)
    # raises error due to len(endog) failing in Statsmodels base classes
    assert_raises(TypeError, check_endog, endog, **kwargs)

    # Example : 1-dim array, both C- and F-contiguous, length 2
    endog = np.array([1.,2.])
    assert_equal(endog.ndim, 1)
    assert_equal(endog.flags['C_CONTIGUOUS'], True)
    assert_equal(endog.flags['F_CONTIGUOUS'], True)
    assert_equal(endog.shape, (2,))
    mod = check_endog(endog, **kwargs)
    mod.filter([])

    # Example : 2-dim array, C-contiguous, long-shaped: (nobs, k_endog)
    endog = np.array([1., 2.]).reshape(2, 1)
    assert_equal(endog.ndim, 2)
    assert_equal(endog.flags['C_CONTIGUOUS'], True)
    # On newer numpy (>= 0.10), this array is (rightly) both C and F contiguous
    # assert_equal(endog.flags['F_CONTIGUOUS'], False)
    assert_equal(endog.shape, (2, 1))
    mod = check_endog(endog, **kwargs)
    mod.filter([])

    # Example : 2-dim array, C-contiguous, wide-shaped: (k_endog, nobs)
    endog = np.array([1., 2.]).reshape(1, 2)
    assert_equal(endog.ndim, 2)
    assert_equal(endog.flags['C_CONTIGUOUS'], True)
    # On newer numpy (>= 0.10), this array is (rightly) both C and F contiguous
    # assert_equal(endog.flags['F_CONTIGUOUS'], False)
    assert_equal(endog.shape, (1, 2))
    # raises error because arrays are always interpreted as
    # (nobs, k_endog), which means that k_endog=2 is incompatibile with shape
    # of design matrix (1, 1)
    assert_raises(ValueError, check_endog, endog, **kwargs)

    # Example : 2-dim array, F-contiguous, long-shaped (nobs, k_endog)
    endog = np.array([1., 2.]).reshape(1, 2).transpose()
    assert_equal(endog.ndim, 2)
    # On newer numpy (>= 0.10), this array is (rightly) both C and F contiguous
    # assert_equal(endog.flags['C_CONTIGUOUS'], False)
    assert_equal(endog.flags['F_CONTIGUOUS'], True)
    assert_equal(endog.shape, (2, 1))
    mod = check_endog(endog, **kwargs)
    mod.filter([])

    # Example : 2-dim array, F-contiguous, wide-shaped (k_endog, nobs)
    endog = np.array([1., 2.]).reshape(2, 1).transpose()
    assert_equal(endog.ndim, 2)
    # On newer numpy (>= 0.10), this array is (rightly) both C and F contiguous
    # assert_equal(endog.flags['C_CONTIGUOUS'], False)
    assert_equal(endog.flags['F_CONTIGUOUS'], True)
    assert_equal(endog.shape, (1, 2))
    # raises error because arrays are always interpreted as
    # (nobs, k_endog), which means that k_endog=2 is incompatibile with shape
    # of design matrix (1, 1)
    assert_raises(ValueError, check_endog, endog, **kwargs)

    # Example  (failure): 3-dim array
    endog = np.array([1., 2.]).reshape(2, 1, 1)
    # raises error due to direct ndim check in Statsmodels base classes
    assert_raises(ValueError, check_endog, endog, **kwargs)

    # Example : np.array with 2 columns
    # Update kwargs for k_endog=2
    kwargs2 = {
        'k_states': 1, 'design': [[1], [0.]], 'obs_cov': [[1, 0], [0, 1]],
        'transition': [[1]], 'selection': [[1]], 'state_cov': [[1]],
        'initialization': 'approximate_diffuse'
    }
    endog = np.array([[1., 2.], [3., 4.]])
    mod = check_endog(endog, k_endog=2, **kwargs2)
    mod.filter([])

def test_pandas_endog():
    # Test various types of pandas endog inputs (e.g. TimeSeries, etc.)

    # Example (failure): pandas.Series, no dates
    endog = pd.Series([1., 2.])
    # raises error due to no dates
    warnings.simplefilter('always')
    # assert_raises(ValueError, check_endog, endog, **kwargs)

    # Example : pandas.Series
    dates = pd.date_range(start='1980-01-01', end='1981-01-01', freq='AS')
    endog = pd.Series([1., 2.], index=dates)
    mod = check_endog(endog, **kwargs)
    mod.filter([])

    # Example : pandas.Series, string datatype
    endog = pd.Series(['a'], index=dates)
    # raises error due to direct type casting check in Statsmodels base classes
    assert_raises(ValueError, check_endog, endog, **kwargs)

    # Example : pandas.Series
    endog = pd.Series([1., 2.], index=dates)
    mod = check_endog(endog, **kwargs)
    mod.filter([])

    # Example : pandas.DataFrame with 1 column
    endog = pd.DataFrame({'a': [1., 2.]}, index=dates)
    mod = check_endog(endog, **kwargs)
    mod.filter([])

    # Example (failure): pandas.DataFrame with 2 columns
    endog = pd.DataFrame({'a': [1., 2.], 'b': [3., 4.]}, index=dates)
    # raises error because 2-columns means k_endog=2, but the design matrix
    # set in **kwargs is shaped (1,1)
    assert_raises(ValueError, check_endog, endog, **kwargs)

    # Check behavior of the link maintained between passed `endog` and
    # `mod.endog` arrays
    endog = pd.DataFrame({'a': [1., 2.]}, index=dates)
    mod = check_endog(endog, **kwargs)
    assert_equal(mod.endog.base is not mod.data.orig_endog, True)
    assert_equal(mod.endog.base is not endog, True)
    assert_equal(mod.data.orig_endog.values.base is not endog, True)
    endog.iloc[0, 0] = 2
    # there is no link to mod.endog
    assert_equal(mod.endog, np.r_[1, 2].reshape(2,1))
    # there remains a link to mod.data.orig_endog
    assert_allclose(mod.data.orig_endog, endog)

    # Example : pandas.DataFrame with 2 columns
    # Update kwargs for k_endog=2
    kwargs2 = {
        'k_states': 1, 'design': [[1], [0.]], 'obs_cov': [[1, 0], [0, 1]],
        'transition': [[1]], 'selection': [[1]], 'state_cov': [[1]],
        'initialization': 'approximate_diffuse'
    }
    endog = pd.DataFrame({'a': [1., 2.], 'b': [3., 4.]}, index=dates)
    mod = check_endog(endog, k_endog=2, **kwargs2)
    mod.filter([])

def test_diagnostics():
    mod, res = get_dummy_mod()

    # Make sure method=None selects the appropriate test
    actual = res.test_normality(method=None)
    desired = res.test_normality(method='jarquebera')
    assert_allclose(actual, desired)

    assert_raises(NotImplementedError, res.test_normality, method='invalid')

    actual = res.test_heteroskedasticity(method=None)
    desired = res.test_heteroskedasticity(method='breakvar')
    assert_allclose(actual, desired)

    assert_raises(ValueError, res.test_heteroskedasticity, method=None, alternative='invalid')
    assert_raises(NotImplementedError, res.test_heteroskedasticity, method='invalid')

    actual = res.test_serial_correlation(method=None)
    desired = res.test_serial_correlation(method='ljungbox')
    assert_allclose(actual, desired)

    assert_raises(NotImplementedError, res.test_serial_correlation, method='invalid')

    # Smoke tests for other options
    actual = res.test_heteroskedasticity(method=None, alternative='d', use_f=False)
    desired = res.test_serial_correlation(method='boxpierce')

def test_diagnostics_nile_eviews():
    # Test the diagnostic tests using the Nile dataset. Results are from 
    # "Fitting State Space Models with EViews" (Van den Bossche 2011,
    # Journal of Statistical Software).
    # For parameter values, see Figure 2
    # For Ljung-Box and Jarque-Bera statistics and p-values, see Figure 5
    # The Heteroskedasticity statistic is not provided in this paper.
    niledata = nile.data.load_pandas().data
    niledata.index = pd.date_range('1871-01-01', '1970-01-01', freq='AS')

    mod = MLEModel(niledata['volume'], k_states=1,
        initialization='approximate_diffuse', initial_variance=1e15,
        loglikelihood_burn=1)
    mod.ssm['design', 0, 0] = 1
    mod.ssm['obs_cov', 0, 0] = np.exp(9.600350)
    mod.ssm['transition', 0, 0] = 1
    mod.ssm['selection', 0, 0] = 1
    mod.ssm['state_cov', 0, 0] = np.exp(7.348705)
    res = mod.filter([])

    # Test Ljung-Box
    # Note: only 3 digits provided in the reference paper
    actual = res.test_serial_correlation(method='ljungbox', lags=10)[0, :, -1]
    assert_allclose(actual, [13.117, 0.217], atol=1e-3)

    # Test Jarque-Bera
    actual = res.test_normality(method='jarquebera')[0, :2]
    assert_allclose(actual, [0.041686, 0.979373], atol=1e-5)

def test_diagnostics_nile_durbinkoopman():
    # Test the diagnostic tests using the Nile dataset. Results are from 
    # Durbin and Koopman (2012); parameter values reported on page 37; test
    # statistics on page 40
    niledata = nile.data.load_pandas().data
    niledata.index = pd.date_range('1871-01-01', '1970-01-01', freq='AS')

    mod = MLEModel(niledata['volume'], k_states=1,
        initialization='approximate_diffuse', initial_variance=1e15,
        loglikelihood_burn=1)
    mod.ssm['design', 0, 0] = 1
    mod.ssm['obs_cov', 0, 0] = 15099.
    mod.ssm['transition', 0, 0] = 1
    mod.ssm['selection', 0, 0] = 1
    mod.ssm['state_cov', 0, 0] = 1469.1
    res = mod.filter([])

    # Test Ljung-Box
    # Note: only 3 digits provided in the reference paper
    actual = res.test_serial_correlation(method='ljungbox', lags=9)[0, 0, -1]
    assert_allclose(actual, [8.84], atol=1e-2)

    # Test Jarque-Bera
    # Note: The book reports 0.09 for Kurtosis, because it is reporting the
    # statistic less the mean of the Kurtosis distribution (which is 3).
    norm = res.test_normality(method='jarquebera')[0]
    actual = [norm[0], norm[2], norm[3]]
    assert_allclose(actual, [0.05, -0.03, 3.09], atol=1e-2)

    # Test Heteroskedasticity
    # Note: only 2 digits provided in the book
    actual = res.test_heteroskedasticity(method='breakvar')[0, 0]
    assert_allclose(actual, [0.61], atol=1e-2)

def test_prediction_results():
    # Just smoke tests for the PredictionResults class, which is copied from
    # elsewhere in Statsmodels

    mod, res = get_dummy_mod()
    predict = res.get_prediction()
    summary_frame = predict.summary_frame()
