from 'badass.ioc' generated codes, the only thing to change is usb_conf.h where the following code should be modified as :
/** Alias for memory allocation. */
#define USBD_malloc         (void *)USBD_static_malloc

/** Alias for memory release. */
#define USBD_free          USBD_static_free

This is a BUG of non-alignment which causing USB not recognised (with an error mark in device manager)
