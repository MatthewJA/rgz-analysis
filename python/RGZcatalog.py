'''
RGZcatalog is a pipeline that takes all of the completed RGZ subjects and creates a Mongo database containing
consensus matching information, radio morphology, IR counterpart location, and data from corresponding
AllWISE and SDSS catalogs.
'''

import argparse
import logging, urllib2, time, json, os, datetime
import pymongo
import numpy as np
import StringIO, gzip
from astropy.io import fits
from astropy import wcs, coordinates as coord, units as u
from astropy.cosmology import Planck13 as cosmo

#custom modules for the RGZ catalog pipeline
import catalog_functions as fn #contains miscellaneous helper functions
import processing as p #contains functions that process the data
from find_duplicates import find_duplicates #finds and marks any radio components that are duplicated between sources

from consensus import rgz_path, data_path, db, version, logfile
in_progress_file = '%s/subject_in_progress.txt' % rgz_path

def RGZcatalog(survey='first'):
	if survey not in {'first', 'atlas'}:
		raise ValueError('Survey must be one of "first" or "atlas", got %s' % survey)
	
	#start timer
	starttime = time.time()
	
	#begin logging even if not run from command line
	logging.basicConfig(filename='{}/{}'.format(rgz_path,logfile), level=logging.DEBUG, format='%(asctime)s %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
	logging.captureWarnings(True)
	
	#connect to database of subjects
	subjects = db['radio_subjects']
	consensus = db['consensus{}'.format(version)]
	catalog = db['catalog{}'.format(version)] #this is being populated by this program
	if catalog.count():
		logging.info('Catalog contains entries; appending')
	else:
		catalog.create_index('catalog_id', unique=True)
	
	if survey == 'atlas':
		with open('%s/atlas_subjects.txt' % rgz_path) as f:
			lines = f.readlines()
		pathdict = {}
		for l in lines:
			spl = l.strip()
			pathdict[spl] = '%s/ATLAS/2x2/%s_radio.fits' % (data_path, spl)
	else:
		#get dictionary for finding the path to FITS files and WCS headers
		with open('%s/first_fits.txt' % rgz_path) as f:
			lines = f.readlines()
		
		pathdict = {}
		for l in lines:
			spl = l.split(' ')
			pathdict[spl[1].strip()] = '%s/rgz/raw_images/RGZ-full.%i/FIRST-IMGS/%s.fits' % (data_path, int(spl[0]), spl[1].strip())
	
	#count the number of entries from this run and how many entries are in the catalog total
	count = 0
	if catalog.count() != 0:
		for entry in catalog.find().sort('catalog_id', -1).limit(1):
			IDnumber = entry['catalog_id']
	else:
		IDnumber = 0
	
	#find completed catalog entries so they can be skipped
	consensus_set = set()
	for source in consensus.find():
		consensus_set.add(source['zooniverse_id'])
	catalog_set = set()
	for entry in catalog.find():
		catalog_set.add(entry['zooniverse_id'])
	to_be_completed = consensus_set.difference(catalog_set)
	if os.path.exists(in_progress_file):
		with open(in_progress_file, 'r') as f:
			in_progress_zid = f.read()
		to_be_completed = to_be_completed.union(in_progress_zid)
	to_be_completed = list(to_be_completed)
	
	#iterate through all noncompleted subjects
	for subject in subjects.find({'zooniverse_id': {'$in':to_be_completed} }).batch_size(10):
	#for subject in subjects.find({'zooniverse_id': {'$in': ['ARG00000sl', 'ARG0003f9l']} }):
	#for subject in subjects.find({'zooniverse_id':'ARG00000sl'}): #sample subject with distinct sources
	#for subject in subjects.find({'zooniverse_id':'ARG0003f9l'}): #sample subject with multiple-component source
		
		#mark subject as being in-progress
		with open(in_progress_file, 'w') as f:
			f.write(subject['zooniverse_id'])
		
		#iterate through all consensus groupings
		for source in consensus.find({'zooniverse_id':subject['zooniverse_id'], '%s_id' % survey:{'$exists':True}}):
			
			#do not process if this object in this source is already in the catalog
			process = True
			for i in catalog.find({'zooniverse_id':subject['zooniverse_id']}):
				if i['consensus']['label'] == source['label']:
					process = False
			
			if process:
				
				logging.info('Processing consensus object %s within subject field %s', source['label'], subject['zooniverse_id'])
				
				count += 1
				IDnumber += 1
				
				#display which entry is being processed to see how far the program is
				print 'Processing entry %i (consensus %s in subject %s)' % (IDnumber, source['label'], subject['zooniverse_id'])
				entry = {'catalog_id':IDnumber, 'zooniverse_id':str(subject['zooniverse_id'])}
				
				#find location of FITS file; once non-FIRST sources are included, modify this
				fid = source['%s_id' % survey]
				#if fid[0] == 'F':
				fits_loc = pathdict[fid]
				entry.update({'%s_id' % survey:str(fid)})
				#else:
				#	raise RuntimeError('Not expecting non-FIRST data')
				#	fits_loc = '%s/rgz/raw_images/ATLAS/2x2/%s_radio.fits' % (data_path, fid)
				#	entry.update({'atlas_id':str(fid)})
				
				#find IR counterpart from consensus data, if present
				w = wcs.WCS(fits.getheader(fits_loc, 0)) #gets pixel-to-WCS conversion from header
				ir_coords = source['ir_peak']
				if ir_coords[0] == -99:
					ir_pos = None
					wise_match = None
					sdss_match = None
				else:
					p2w = w.wcs_pix2world
					if survey == 'first':
						ir_ra_pixels = ir_coords[0]*w._naxis1/500.
						ir_dec_pixels = 1 + w._naxis2 - ir_coords[1]*w._naxis2/500.
					else:
						ir_ra_pixels = ir_coords[0] * 200./500.
						ir_dec_pixels = 200. - ir_coords[1] * 200./500.
					ir_peak = p2w( np.array([[ir_ra_pixels, ir_dec_pixels]]), 1)
					ir_pos = coord.SkyCoord(ir_peak[0][0], ir_peak[0][1], unit=(u.deg,u.deg), frame='icrs')
				
				entry.update({'consensus':{'n_radio':source['n_votes'], 'n_total':source['n_total'], 'n_ir':source['n_ir'], 'ir_flag':source['ir_flag'], \
										   'ir_level':source['ir_level'], 'radio_level':source['consensus_level'], 'label':source['label']}})
				if ir_pos:
					logging.info('IR counterpart found')
					entry['consensus'].update({'ir_ra':ir_pos.ra.deg, 'ir_dec':ir_pos.dec.deg})
				else:
					logging.info('No IR counterpart found')
				
				#if an IR peak exists, search AllWISE and SDSS for counterparts
				if ir_pos and survey == 'first':
					
					wise_match = p.getWISE(entry)
					if wise_match:
						designation = wise_match['designation'][5:]
						pz = db['wise_pz'].find_one({'wiseX':designation})
						if pz is not None:
							wise_match['photo_redshift'] = pz['zPhoto_Corr']
						entry.update({'AllWISE':wise_match})
					
					tryCount = 0
					while(True):
						tryCount += 1
						try:
							sdss_match = p.getSDSS(entry)
							if sdss_match:
								entry.update({'SDSS':sdss_match})
							break
						except KeyError as e:
							if tryCount>5:
								output('Bad response from SkyServer; trying again in 10 min', logging.exception)
								raise fn.DataAccessError(message)
							elif e.message == 'ra':
								#unable to reproduce; no error when I try again, so let's just do that
								logging.exception(e)
								time.sleep(10)
							else:
								raise e

				elif ir_pos and survey == 'atlas':
					swire_match = p.getSWIRE(entry)
					if swire_match:
						entry.update({'SWIRE':swire_match})
				
				#try block attempts to read JSON from web; if it exists, calculate data
				try:
					link = subject['location']['contours'] #gets url as Unicode string
					
					# Use local file if available
					
					jsonfile = link.split("/")[-1]
					jsonfile_path = "{0}/rgz/contours/{1}".format(data_path,jsonfile)
					if os.path.exists(jsonfile_path):
						with open(jsonfile_path,'r') as jf:
							data = json.load(jf)
					
					# Otherwise, read from web
					
					else:
						
						# Reform weblink to point to the direct S3 URL, which will work even with older SSLv3
						
						link_s3 = "http://zooniverse-static.s3.amazonaws.com/"+link.split('http://')[-1]
						
						tryCount = 0
						while(True): #in case of error, wait 10 sec and try again; give up after 5 tries
							tryCount += 1
							try:
								compressed = urllib2.urlopen(str(link_s3)).read() #reads contents of url to str
								break
							except (urllib2.URLError, urllib2.HTTPError) as e:
								if tryCount>5:
									output('Unable to connect to Amazon Web Services; trying again in 10 min', logging.exception)
									raise fn.DataAccessError(message)
								logging.exception(e)
								time.sleep(10)
						
						tempfile = StringIO.StringIO(compressed) #temporarily stores contents as file (emptied after unzipping)
						uncompressed = gzip.GzipFile(fileobj=tempfile, mode='r').read() #unzips contents to str
						data = json.loads(uncompressed) #loads JSON object
					
					radio_data = p.getRadio(data, fits_loc, source)
					entry.update(radio_data)
					
					#check if a component is straddling the edge of the image
					entry.update({'overedge':0})
					source_bbox = np.array(source['bbox'])
					for c in data['contours']:
						bbox = np.array(c[0]['bbox'])
						if bbox in source_bbox:
							vertices = []
							for pos in c[0]['arr']:
								vertices.append([pos['x'], pos['y']])
							vertices = np.array(vertices)
							diff = vertices[0] - vertices[-1]
							if np.sqrt(diff[0]**2 + diff[1]**2) > 1 and (np.any(vertices[0] <= 4) or np.any(vertices[0] >= 128)):
								entry.update({'overedge':1})
								break
					
					#use WISE catalog name if available
					if ir_pos and survey == 'first' and wise_match:  # short-circuits
						entry.update({'rgz_name':'RGZ{}{}'.format(wise_match['designation'][5:14], wise_match['designation'][15:22])})
					elif ir_pos and survey == 'atlas' and swire_match:
						entry.update({'rgz_name':'RGZ{}{}'.format(swire_match['designation'][7:17], swire_match['designation'][17:26])})
					else:
						#if not, try consensus IR position
						if ir_pos:
							ra = ir_pos.ra.deg
							dec = ir_pos.dec.deg
						#finally, just use radio center
						else:
							ra = radio_data['radio']['ra']
							dec = radio_data['radio']['dec']
						
						ra_h = int(ra/15.)
						ra_m = int((ra - ra_h*15)*4)
						ra_s = (ra - ra_h*15 - ra_m/4.)*240
						if dec < 0:
							dec_ = -dec
							sign = '-'
						else:
							dec_ = dec
							sign = '+'
						dec_d = int(dec_)
						dec_m = int((dec_ - dec_d)*60)
						dec_s = int((dec_ - dec_d - dec_m/60.)*3600)
						entry.update({'rgz_name':'RGZJ{:0=2}{:0=2}{:0=4.1f}{:0=+3}{:0=2}{:0=2}'.format(ra_h, ra_m, ra_s, dec_d, dec_m, dec_s)})
					
					if survey == 'first':
						#calculate physical data using redshift from SDSS
						if sdss_match:
							z = 0
							if 'spec_redshift' in sdss_match:
								z = sdss_match['spec_redshift']
							elif 'photo_redshift' in sdss_match:
								z = sdss_match['photo_redshift']
							if z>0:
								DAkpc = float(cosmo.angular_diameter_distance(z)/u.kpc) #angular diameter distance in kpc
								DLm = float(cosmo.luminosity_distance(z)/u.m) #luminosity distance in m
								maxPhysicalExtentKpc = DAkpc*radio_data['radio']['max_angular_extent']*np.pi/180/3600 #arcseconds to radians
								totalCrossSectionKpc2 = np.square(DAkpc)*radio_data['radio']['total_solid_angle']*np.square(np.pi/180/3600) #arcseconds^2 to radians^2
								totalLuminosityWHz = radio_data['radio']['total_flux']*1e-29*4*np.pi*np.square(DLm) #mJy to W/(m^2 Hz), kpc to m
								totalLuminosityErrWHz = radio_data['radio']['total_flux_err']*1e-29*4*np.pi*np.square(DLm)
								peakLuminosityErrWHz = radio_data['radio']['peak_flux_err']*1e-29*4*np.pi*np.square(DLm)
								for component in radio_data['radio']['components']:
									component['physical_extent'] = DAkpc*component['angular_extent']*np.pi/180/3600
									component['cross_section'] = np.square(DAkpc)*component['solid_angle']*np.square(np.pi/180/3600)
									component['luminosity'] = component['flux']*1e-29*4*np.pi*np.square(DLm)
									component['luminosity_err'] = component['flux_err']*1e-29*4*np.pi*np.square(DLm)
								for peak in radio_data['radio']['peaks']:
									peak['luminosity'] = peak['flux']*1e-29*4*np.pi*np.square(DLm)
								entry['radio'].update({'max_physical_extent':maxPhysicalExtentKpc, 'total_cross_section':totalCrossSectionKpc2, \
													   'total_luminosity':totalLuminosityWHz, 'total_luminosity_err':totalLuminosityErrWHz, \
													   'peak_luminosity_err':peakLuminosityErrWHz})
						
					logging.info('Radio data added')
									   
				#if the link doesn't have a JSON, no data can be determined
				except urllib2.HTTPError as e:
					if e.code == 404:
						logging.info('No radio JSON detected')
					else:
						logging.exception(e)
						raise
				
				catalog.insert(entry)
				find_duplicates(entry['zooniverse_id'])
				logging.info('Entry %i added to catalog', IDnumber)
		
		with open(in_progress_file, 'w') as f:
			f.write('')
		
	#end timer
	endtime = time.time()
	output('Time taken: %f' % (endtime-starttime))
	
	return count

def output(string, fn=logging.info):
	'''
	Print a string to screen and the logfile
	'''
	fn(string)
	print string

if __name__ == '__main__':
	logging.basicConfig(filename='{}/{}'.format(rgz_path,logfile), level=logging.DEBUG, format='%(asctime)s: %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
	logging.captureWarnings(True)
	logging.info('Catalog run from command line')
	
	parser = argparse.ArgumentParser()
        parser.add_argument(
                'survey', choices={'first', 'atlas'},
                help='Survey to generate catalog for')
        args = parser.parse_args()

	assert db['radio_subjects'].count()>0, 'RGZ subjects collection not in Mongo database'
	assert db['consensus{}'.format(version)].count()>0, 'RGZ consensus{} collection not in Mongo database'.format(version)
	
	if args.survey == 'first':
		assert db['wise_pz'].count()>0, 'WISExSCOSPZ catalog not in Mongo database'

	done = False
	while not done:
		try:
			output('%i entries added.' % RGZcatalog(survey=args.survey))
			done = True
		except pymongo.errors.CursorNotFound as c:
			time.sleep(10)
			output('Cursor timed out; starting again.')
		except fn.DataAccessError as d:
			resume = datetime.datetime.now() + datetime.timedelta(minutes=10)
			output("RGZcatalog.py can't connect to external server; will resume at {:%H:%M}".format(resume))
			time.sleep(600)
		except BaseException as e:
			logging.exception(e)
			raise
